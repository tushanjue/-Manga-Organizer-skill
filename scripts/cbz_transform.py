#!/usr/bin/env python3
"""Safe, JSON-only transformations for already-inspected CBZ archives.

The CLI intentionally performs no library discovery.  Every source, destination,
boundary, identity change, and trust decision must be supplied explicitly.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET


IDENTITY_FIELDS = (
    "Title",
    "Series",
    "LocalizedSeries",
    "SeriesSort",
    "Number",
    "Volume",
    "Count",
    "Format",
)
IDENTITY_FIELD_LOOKUP = {field.casefold(): field for field in IDENTITY_FIELDS}
IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".bmp",
    ".tif",
    ".tiff",
}
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBER_EXPANSION_RATIO = 1_000.0
COPY_CHUNK_BYTES = 1024 * 1024
SPECIAL_NUMBER_RE = re.compile(r"SP[0-9]+", re.IGNORECASE)
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
XML_DECLARATION_RE = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class TransformError(Exception):
    """A controlled failure that can be emitted as machine-readable JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class CleanParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep help and parser errors machine-readable too."""

    def print_help(self, file: Any = None) -> None:
        _emit(
            {
                "ok": True,
                "kind": "help",
                "prog": self.prog,
                "help": self.format_help(),
            }
        )

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            raise TransformError("CLI_USAGE", message.strip())
        raise CleanParserExit(status)

    def error(self, message: str) -> None:
        raise TransformError(
            "CLI_USAGE",
            message,
            {"usage": self.format_usage().strip()},
        )


def _duplicate_rejecting_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransformError(
                "JSON_DUPLICATE_KEY",
                "JSON objects must not contain duplicate keys",
                {"key": key},
            )
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise TransformError("INPUT_NOT_FILE", "JSON input is not a regular file", {"path": str(path)})
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise TransformError(
            "JSON_TOO_LARGE",
            "JSON input exceeds the safety limit",
            {"path": str(path), "bytes": size, "limit": MAX_JSON_BYTES},
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TransformError("JSON_READ_FAILED", "Could not read UTF-8 JSON", {"path": str(path), "reason": str(exc)}) from exc
    try:
        return json.loads(text, object_pairs_hook=_duplicate_rejecting_object)
    except TransformError:
        raise
    except json.JSONDecodeError as exc:
        raise TransformError(
            "JSON_INVALID",
            "JSON input is invalid",
            {"path": str(path), "line": exc.lineno, "column": exc.colno, "reason": exc.msg},
        ) from exc


def _resolved_input(path_text: str, base: Path | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TransformError("INPUT_NOT_FOUND", "Input path could not be resolved", {"path": str(candidate), "reason": str(exc)}) from exc
    if not resolved.is_file():
        raise TransformError("INPUT_NOT_FILE", "Input path is not a regular file", {"path": str(resolved)})
    return resolved


def _resolved_output(path_text: str, base: Path | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise TransformError("OUTPUT_PATH_INVALID", "Output path could not be resolved", {"path": str(candidate), "reason": str(exc)}) from exc
    if resolved.suffix.casefold() != ".cbz":
        raise TransformError("OUTPUT_EXTENSION", "Output must use the .cbz extension", {"path": str(resolved)})
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise TransformError("FILE_HASH_FAILED", "Could not hash file", {"path": str(path), "reason": str(exc)}) from exc
    return digest.hexdigest()


def _safe_zip_member(name: str) -> None:
    if not name or "\x00" in name or any(ord(char) < 32 for char in name):
        raise TransformError("ARC008", "Archive contains an empty or control-character member path", {"member": name})
    if "\\" in name:
        raise TransformError("ARC008", "Archive member paths must use forward slashes", {"member": name})
    if name.startswith("/") or name.startswith("//") or WINDOWS_DRIVE_RE.match(name):
        raise TransformError("ARC008", "Archive contains an absolute member path", {"member": name})
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed:
        raise TransformError("ARC008", "Archive contains an unsafe root directory entry", {"member": name})
    parts = trimmed.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise TransformError("ARC008", "Archive contains an unsafe member path component", {"member": name})
    normalized = PurePosixPath(trimmed).as_posix()
    if normalized != trimmed:
        raise TransformError("ARC008", "Archive member path is not normalized", {"member": name})


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return info.create_system == 3 and stat.S_ISLNK(unix_mode)


def _hash_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise TransformError(
            "ARC002",
            "Archive member could not be read and verified",
            {"member": info.filename, "reason": str(exc)},
        ) from exc
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_comicinfo(xml_bytes: bytes, archive_path: Path) -> ET.Element:
    if len(xml_bytes) > MAX_XML_BYTES:
        raise TransformError(
            "META005",
            "ComicInfo.xml exceeds the safety limit",
            {"archive": str(archive_path), "bytes": len(xml_bytes), "limit": MAX_XML_BYTES},
        )
    try:
        xml_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TransformError(
            "META005",
            "ComicInfo.xml must be UTF-8 encoded",
            {"archive": str(archive_path), "reason": str(exc)},
        ) from exc
    if XML_DECLARATION_RE.search(xml_bytes):
        raise TransformError(
            "META005",
            "ComicInfo.xml must not contain a doctype or entity declaration",
            {"archive": str(archive_path)},
        )
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise TransformError(
            "META005",
            "ComicInfo.xml is not well-formed XML",
            {"archive": str(archive_path), "reason": str(exc)},
        ) from exc
    if _local_name(root.tag) != "ComicInfo":
        raise TransformError(
            "META005",
            "ComicInfo.xml root element must be ComicInfo",
            {"archive": str(archive_path), "root": _local_name(root.tag)},
        )
    return root


@dataclass(frozen=True)
class MemberSnapshot:
    info: zipfile.ZipInfo
    sha256: str

    @property
    def is_image(self) -> bool:
        return not self.info.is_dir() and PurePosixPath(self.info.filename).suffix.casefold() in IMAGE_SUFFIXES


@dataclass
class ArchiveSnapshot:
    path: Path
    archive_sha256: str
    members: list[MemberSnapshot]
    comicinfo_index: int
    comicinfo_bytes: bytes
    comicinfo_root: ET.Element
    comment: bytes

    @property
    def images(self) -> list[MemberSnapshot]:
        return [member for member in self.members if member.is_image]


def _inspect_archive(path: Path) -> ArchiveSnapshot:
    try:
        archive_size = path.stat().st_size
    except OSError as exc:
        raise TransformError("INPUT_STAT_FAILED", "Could not inspect archive file", {"path": str(path), "reason": str(exc)}) from exc
    try:
        with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
            infos = archive.infolist()
            if not infos:
                raise TransformError("ARC002", "Archive is empty", {"path": str(path)})
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise TransformError(
                    "ARC007",
                    "Archive contains too many members",
                    {"path": str(path), "members": len(infos), "limit": MAX_ARCHIVE_MEMBERS},
                )
            total_uncompressed = 0
            seen_names: dict[str, str] = {}
            comicinfo_indexes: list[int] = []
            for index, info in enumerate(infos):
                _safe_zip_member(info.filename)
                folded_name = info.filename.casefold()
                if folded_name in seen_names:
                    raise TransformError(
                        "ARC008",
                        "Archive contains duplicate or case-colliding member paths",
                        {"member": info.filename, "conflicts_with": seen_names[folded_name]},
                    )
                seen_names[folded_name] = info.filename
                if info.flag_bits & 0x1:
                    raise TransformError("ARC003", "Encrypted archive members are not supported", {"member": info.filename})
                if _is_zip_symlink(info):
                    raise TransformError("ARC008", "Archive contains a symbolic link", {"member": info.filename})
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise TransformError(
                        "ARC007",
                        "Archive uncompressed size exceeds the safety limit",
                        {"path": str(path), "bytes": total_uncompressed, "limit": MAX_ARCHIVE_UNCOMPRESSED_BYTES},
                    )
                if info.file_size and info.compress_size:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_MEMBER_EXPANSION_RATIO:
                        raise TransformError(
                            "ARC007",
                            "Archive member expansion ratio exceeds the safety limit",
                            {"member": info.filename, "ratio": ratio, "limit": MAX_MEMBER_EXPANSION_RATIO},
                        )
                if PurePosixPath(info.filename).name.casefold() == "comicinfo.xml":
                    comicinfo_indexes.append(index)
            if not comicinfo_indexes:
                raise TransformError("ARC005", "Archive must contain one root ComicInfo.xml", {"path": str(path)})
            if len(comicinfo_indexes) != 1:
                raise TransformError(
                    "ARC006",
                    "Archive contains multiple ComicInfo.xml members",
                    {"path": str(path), "members": [infos[index].filename for index in comicinfo_indexes]},
                )
            comicinfo_index = comicinfo_indexes[0]
            if infos[comicinfo_index].filename != "ComicInfo.xml":
                raise TransformError(
                    "ARC005",
                    "ComicInfo.xml must use exact capitalization at the archive root",
                    {"path": str(path), "member": infos[comicinfo_index].filename},
                )
            try:
                bad_member = archive.testzip()
            except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                raise TransformError("ARC002", "Archive integrity verification failed", {"path": str(path), "reason": str(exc)}) from exc
            if bad_member is not None:
                raise TransformError("ARC002", "Archive CRC verification failed", {"path": str(path), "member": bad_member})
            members = [MemberSnapshot(copy.copy(info), _hash_zip_member(archive, info)) for info in infos]
            try:
                xml_bytes = archive.read(infos[comicinfo_index])
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise TransformError("ARC002", "ComicInfo.xml could not be read", {"path": str(path), "reason": str(exc)}) from exc
            root = _parse_comicinfo(xml_bytes, path)
            comment = bytes(archive.comment)
    except TransformError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError) as exc:
        raise TransformError(
            "ARC002",
            "Input is not a readable, supported CBZ/ZIP archive",
            {"path": str(path), "bytes": archive_size, "reason": str(exc)},
        ) from exc
    return ArchiveSnapshot(path, _sha256_file(path), members, comicinfo_index, xml_bytes, root, comment)


def _identity_snapshot(root: ET.Element) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for field in IDENTITY_FIELDS:
        matches = [child for child in list(root) if _local_name(child.tag) == field]
        if len(matches) > 1:
            raise TransformError(
                "META005",
                "ComicInfo.xml contains duplicate identity elements",
                {"field": field, "count": len(matches)},
            )
        values[field] = matches[0].text if matches else None
    return values


def _canonical_patch(raw: Any) -> dict[str, str | None]:
    if not isinstance(raw, dict):
        raise TransformError("PATCH_INVALID", "Identity patch must be a JSON object")
    if set(raw) == {"identity"}:
        raw = raw["identity"]
        if not isinstance(raw, dict):
            raise TransformError("PATCH_INVALID", "The identity property must be a JSON object")
    result: dict[str, str | None] = {}
    for supplied_key, value in raw.items():
        if not isinstance(supplied_key, str):
            raise TransformError("PATCH_INVALID", "Identity patch keys must be strings")
        canonical = IDENTITY_FIELD_LOOKUP.get(supplied_key.casefold())
        if canonical is None:
            raise TransformError(
                "PATCH_FIELD_NOT_ALLOWED",
                "Identity patch contains a non-identity or unsupported field",
                {"field": supplied_key, "allowed_fields": list(IDENTITY_FIELDS)},
            )
        if canonical in result:
            raise TransformError("PATCH_INVALID", "Identity patch supplies the same field more than once", {"field": canonical})
        if value is None:
            result[canonical] = None
        elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise TransformError(
                "PATCH_VALUE_INVALID",
                "Identity values must be strings, finite numbers, or null",
                {"field": canonical},
            )
        elif isinstance(value, float) and not math.isfinite(value):
            raise TransformError("PATCH_VALUE_INVALID", "Identity numbers must be finite", {"field": canonical})
        else:
            text_value = str(value)
            if not text_value.strip():
                raise TransformError(
                    "PATCH_VALUE_INVALID",
                    "Use null to remove an identity field; empty values are not accepted",
                    {"field": canonical},
                )
            result[canonical] = text_value
    if not result:
        raise TransformError("PATCH_EMPTY", "Identity patch does not contain any changes")
    return result


def _namespace_prefix(root: ET.Element) -> str:
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag.split("}", 1)[0] + "}"
    return ""


def _set_direct_child(root: ET.Element, field: str, value: str | None) -> None:
    matches = [child for child in list(root) if _local_name(child.tag) == field]
    if len(matches) > 1:
        raise TransformError("META005", "ComicInfo.xml contains duplicate elements", {"field": field})
    if value is None:
        if matches:
            root.remove(matches[0])
        return
    if matches:
        element = matches[0]
    else:
        element = ET.SubElement(root, _namespace_prefix(root) + field)
    element.text = value


def _apply_patch(root: ET.Element, patch: Mapping[str, str | None]) -> None:
    _identity_snapshot(root)
    for field, value in patch.items():
        _set_direct_child(root, field, value)


def _remove_pages(root: ET.Element) -> None:
    for child in list(root):
        if _local_name(child.tag) == "Pages":
            root.remove(child)


def _serialize_comicinfo(root: ET.Element) -> bytes:
    output = io.BytesIO()
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    xml_bytes = output.getvalue()
    _parse_comicinfo(xml_bytes, Path("ComicInfo.xml"))
    return xml_bytes


def _clone_info(info: zipfile.ZipInfo, filename: str | None = None) -> zipfile.ZipInfo:
    target_name = filename if filename is not None else info.filename
    cloned = zipfile.ZipInfo(target_name, date_time=info.date_time)
    cloned.compress_type = info.compress_type
    cloned.comment = bytes(info.comment)
    cloned.extra = bytes(info.extra)
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    cloned.create_version = info.create_version
    cloned.extract_version = info.extract_version
    cloned.volume = info.volume
    return cloned


@dataclass(frozen=True)
class OutputEntry:
    info: zipfile.ZipInfo
    source_path: Path | None = None
    source_member: str | None = None
    data: bytes | None = None

    def __post_init__(self) -> None:
        source_backed = self.source_path is not None and self.source_member is not None
        data_backed = self.data is not None
        if source_backed == data_backed:
            raise ValueError("OutputEntry must have exactly one data source")


@dataclass
class StagedArchive:
    destination: Path
    temporary: Path
    snapshot: ArchiveSnapshot


def _write_entry(
    output: zipfile.ZipFile,
    entry: OutputEntry,
    sources: dict[Path, zipfile.ZipFile],
) -> None:
    _safe_zip_member(entry.info.filename)
    if entry.data is not None:
        output.writestr(entry.info, entry.data)
        return
    assert entry.source_path is not None and entry.source_member is not None
    source_archive = sources[entry.source_path]
    try:
        with source_archive.open(entry.source_member, "r") as source_stream:
            with output.open(entry.info, "w", force_zip64=True) as output_stream:
                shutil.copyfileobj(source_stream, output_stream, COPY_CHUNK_BYTES)
    except (OSError, RuntimeError, KeyError, zipfile.BadZipFile) as exc:
        raise TransformError(
            "ARC_WRITE_FAILED",
            "Could not copy an archive member into the staged output",
            {"source": str(entry.source_path), "member": entry.source_member, "reason": str(exc)},
        ) from exc


def _stage_archive(destination: Path, entries: Sequence[OutputEntry], comment: bytes) -> StagedArchive:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
    except OSError as exc:
        raise TransformError(
            "OUTPUT_STAGE_FAILED",
            "Could not create a temporary output beside the destination",
            {"destination": str(destination), "reason": str(exc)},
        ) from exc
    temporary = Path(temporary_name)
    try:
        with contextlib.ExitStack() as stack:
            source_paths = sorted(
                {entry.source_path for entry in entries if entry.source_path is not None},
                key=lambda path: str(path),
            )
            sources = {
                source_path: stack.enter_context(zipfile.ZipFile(source_path, "r", allowZip64=True))
                for source_path in source_paths
            }
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as output:
                output.comment = comment
                for entry in entries:
                    _write_entry(output, entry, sources)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        snapshot = _inspect_archive(temporary)
        return StagedArchive(destination, temporary, snapshot)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _commit_staged(staged: Sequence[StagedArchive], overwrite: bool) -> None:
    try:
        if not overwrite:
            collisions = [str(item.destination) for item in staged if item.destination.exists()]
            if collisions:
                raise TransformError(
                    "NAME005",
                    "Output collision detected immediately before atomic replacement",
                    {"outputs": collisions},
                )
        for item in staged:
            os.replace(item.temporary, item.destination)
        for parent in {item.destination.parent for item in staged}:
            try:
                directory_fd = os.open(parent, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        for item in staged:
            if item.temporary.exists():
                try:
                    item.temporary.unlink()
                except OSError:
                    pass


def _check_output_set(outputs: Sequence[Path], sources: Sequence[Path], overwrite: bool) -> None:
    folded: dict[str, str] = {}
    for output in outputs:
        key = os.path.normcase(str(output)).casefold()
        if key in folded:
            raise TransformError(
                "NAME005",
                "Two planned outputs resolve to the same path",
                {"output": str(output), "conflicts_with": folded[key]},
            )
        folded[key] = str(output)
        if any(output == source for source in sources):
            raise TransformError(
                "SOURCE_REPLACEMENT_REFUSED",
                "An output must not replace a supplied source archive",
                {"output": str(output)},
            )
        if output.exists():
            if output.is_dir():
                raise TransformError("NAME005", "Output path is an existing directory", {"output": str(output)})
            if not overwrite:
                raise TransformError(
                    "NAME005",
                    "Output already exists; pass --overwrite only after confirming the target",
                    {"output": str(output)},
                )


def _member_hash_records(snapshot: ArchiveSnapshot) -> list[dict[str, Any]]:
    return [
        {"index": index, "name": member.info.filename, "sha256": member.sha256}
        for index, member in enumerate(snapshot.members)
    ]


def _source_unchanged(snapshot: ArchiveSnapshot) -> bool:
    return _sha256_file(snapshot.path) == snapshot.archive_sha256


def _normalize(args: argparse.Namespace) -> dict[str, Any]:
    source = _resolved_input(args.source)
    output = _resolved_output(args.output)
    patch_path = _resolved_input(args.patch)
    raw_patch = _load_json(patch_path)
    change_reason = "explicit user-authorized identity normalization"
    if isinstance(raw_patch, dict) and "identity" in raw_patch:
        patch = _canonical_patch(raw_patch["identity"])
        supplied_reason = raw_patch.get("change_reason")
        if not isinstance(supplied_reason, str) or not supplied_reason.strip():
            raise TransformError("PATCH_INVALID", "Wrapped identity patch requires a non-empty change_reason")
        change_reason = supplied_reason.strip()
    else:
        patch = _canonical_patch(raw_patch)
    _check_output_set([output], [source], args.overwrite)
    before = _inspect_archive(source)
    root = copy.deepcopy(before.comicinfo_root)
    identity_before = _identity_snapshot(root)
    _apply_patch(root, patch)
    source_volume_provenance: str | None = None
    if "Volume" in patch and patch["Volume"] is None and identity_before.get("Volume"):
        source_volume_provenance = f"Source volume: {identity_before['Volume']}"
        notes_nodes = [child for child in list(root) if _local_name(child.tag) == "Notes"]
        if len(notes_nodes) > 1:
            raise TransformError("META005", "ComicInfo.xml contains duplicate Notes elements")
        current_notes = (notes_nodes[0].text or "").strip() if notes_nodes else ""
        if source_volume_provenance not in current_notes:
            merged_notes = f"{current_notes}\n{source_volume_provenance}".strip()
            _set_direct_child(root, "Notes", merged_notes)
    identity_after = _identity_snapshot(root)
    if identity_after == identity_before:
        raise TransformError(
            "PATCH_NO_CHANGE",
            "Identity patch would not change ComicInfo.xml",
            {"identity": identity_before},
        )
    xml_after = _serialize_comicinfo(root)
    entries: list[OutputEntry] = []
    for index, member in enumerate(before.members):
        if index == before.comicinfo_index:
            entries.append(OutputEntry(_clone_info(member.info), data=xml_after))
        elif member.info.is_dir():
            entries.append(OutputEntry(_clone_info(member.info), data=b""))
        else:
            entries.append(
                OutputEntry(
                    _clone_info(member.info),
                    source_path=source,
                    source_member=member.info.filename,
                )
            )
    planned_integrity = []
    xml_hash_after = hashlib.sha256(xml_after).hexdigest()
    for index, member in enumerate(before.members):
        after_hash = xml_hash_after if index == before.comicinfo_index else member.sha256
        planned_integrity.append(
            {
                "index": index,
                "name": member.info.filename,
                "metadata": index == before.comicinfo_index,
                "member_index": index,
                "member_name": member.info.filename,
                "is_metadata": index == before.comicinfo_index,
                "before_sha256": member.sha256,
                "after_sha256": after_hash,
                "unchanged": member.sha256 == after_hash,
                "content_unchanged": member.sha256 == after_hash,
                "member_order_unchanged": True,
            }
        )
    result: dict[str, Any] = {
        "ok": True,
        "command": "normalize",
        "dry_run": bool(args.dry_run),
        "source": str(source),
        "output": str(output),
        "old_output": str(source),
        "new_output": str(output),
        "source_sha256": before.archive_sha256,
        "identity_patch": patch,
        "identity_before": identity_before,
        "identity_after": identity_after,
        "changed_fields": [field for field in IDENTITY_FIELDS if identity_before.get(field) != identity_after.get(field)]
        + (["Notes"] if source_volume_provenance else []),
        "change_reason": change_reason,
        "source_volume_provenance": source_volume_provenance,
        "member_order_before": [member.info.filename for member in before.members],
        "member_order_after": [member.info.filename for member in before.members],
        "member_integrity": planned_integrity,
        "non_xml_bytes_preserved": True,
        "source_unchanged": None,
        "written": False,
    }
    if args.dry_run:
        result["source_unchanged"] = _source_unchanged(before)
        return result
    staged = _stage_archive(output, entries, before.comment)
    after = staged.snapshot
    before_names = [member.info.filename for member in before.members]
    after_names = [member.info.filename for member in after.members]
    if after_names != before_names:
        staged.temporary.unlink(missing_ok=True)
        raise TransformError("ARC010", "Metadata normalization changed archive member order")
    verified_integrity = []
    for index, (old_member, new_member) in enumerate(zip(before.members, after.members)):
        metadata = index == before.comicinfo_index
        unchanged = old_member.sha256 == new_member.sha256
        if not metadata and not unchanged:
            staged.temporary.unlink(missing_ok=True)
            raise TransformError(
                "ARC010",
                "Metadata normalization changed a non-ComicInfo member",
                {"member": old_member.info.filename},
            )
        verified_integrity.append(
            {
                "index": index,
                "name": old_member.info.filename,
                "metadata": metadata,
                "member_index": index,
                "member_name": old_member.info.filename,
                "is_metadata": metadata,
                "before_sha256": old_member.sha256,
                "after_sha256": new_member.sha256,
                "unchanged": unchanged,
                "content_unchanged": unchanged,
                "member_order_unchanged": True,
            }
        )
    if _identity_snapshot(after.comicinfo_root) != identity_after:
        staged.temporary.unlink(missing_ok=True)
        raise TransformError("ARC010", "Staged ComicInfo identity does not match the requested patch")
    _commit_staged([staged], args.overwrite)
    result.update(
        {
            "member_integrity": verified_integrity,
            "output_sha256": after.archive_sha256,
            "source_unchanged": _source_unchanged(before),
            "written": True,
        }
    )
    return result


def _first_present(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _required_page_number(item: Mapping[str, Any], names: Sequence[str], item_index: int) -> int:
    value = _first_present(item, names)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TransformError(
            "BOUNDARY_INVALID",
            "Boundary page indices must be positive integers",
            {"item_index": item_index, "accepted_fields": list(names), "value": value},
        )
    return value


def _truthy_visual_pair(value: Any) -> bool:
    if value is True:
        return True
    if not isinstance(value, dict):
        return False
    start = _first_present(value, ("start", "start_page", "first", "first_page", "before"))
    end = _first_present(value, ("end", "end_page", "last", "last_page", "after"))
    boundary = _first_present(value, ("boundary", "boundary_near", "boundaries"))
    if start is True and end is True:
        return boundary is not False
    return False


def _visual_evidence(plan: Mapping[str, Any], item: Mapping[str, Any], item_index: int) -> dict[str, Any]:
    if plan.get("visual_review_complete") is True:
        return {"scope": "plan", "field": "visual_review_complete", "verified": True}
    for key in ("visual_validation", "visual_boundary_flags", "visual_checks"):
        if key in plan and _truthy_visual_pair(plan[key]):
            return {"scope": "plan", "field": key, "verified": True}
    if item.get("visual_boundary_verified") is True or item.get("visual_review_complete") is True:
        field = "visual_boundary_verified" if item.get("visual_boundary_verified") is True else "visual_review_complete"
        return {"scope": "item", "field": field, "verified": True}
    start = _first_present(item, ("visual_start_verified", "start_visual_verified", "first_page_visual_verified"))
    end = _first_present(item, ("visual_end_verified", "end_visual_verified", "last_page_visual_verified"))
    if start is True and end is True:
        return {"scope": "item", "field": "start/end visual flags", "verified": True}
    for key in ("visual_validation", "visual_boundary_flags", "visual_checks"):
        if key in item and _truthy_visual_pair(item[key]):
            return {"scope": "item", "field": key, "verified": True}
    raise TransformError(
        "BOUNDARY_VISUAL_REVIEW_REQUIRED",
        "Every split span must carry affirmative visual boundary flags from the plan",
        {"item_index": item_index},
    )


def _item_identity(item: Mapping[str, Any], kind: str, item_index: int) -> dict[str, str | None]:
    raw_identity = item.get("identity", {})
    if not isinstance(raw_identity, dict):
        raise TransformError("BOUNDARY_INVALID", "Boundary identity must be an object", {"item_index": item_index})
    patch = _canonical_patch(raw_identity) if raw_identity else {}
    supplied_number = _first_present(item, ("number", "chapter", "special_number"))
    if supplied_number is not None:
        if isinstance(supplied_number, bool) or not isinstance(supplied_number, (str, int, float)):
            raise TransformError("BOUNDARY_INVALID", "Boundary number must be a string or number", {"item_index": item_index})
        number_text = str(supplied_number)
        if "Number" in patch and patch["Number"] != number_text:
            raise TransformError("BOUNDARY_INVALID", "Boundary number conflicts with identity.Number", {"item_index": item_index})
        patch["Number"] = number_text
    number = patch.get("Number")
    if not number:
        raise TransformError("BOUNDARY_INVALID", "Each chapter or Special requires an explicit Number", {"item_index": item_index})
    if kind == "special":
        if not SPECIAL_NUMBER_RE.fullmatch(number):
            raise TransformError("BOUNDARY_INVALID", "Special Number must use SP followed by digits", {"item_index": item_index, "number": number})
        if patch.get("Format") not in (None, "Special"):
            raise TransformError("BOUNDARY_INVALID", "Special Format cannot be overridden", {"item_index": item_index})
        patch["Number"] = number.upper()
        patch["Format"] = "Special"
        title = patch.get("Title")
        if not title or re.search(r"[\u3400-\u9fff]", title) is None:
            raise TransformError(
                "META008",
                "A Special split requires an explicit reliable Chinese Title",
                {"item_index": item_index},
            )
    else:
        if SPECIAL_NUMBER_RE.fullmatch(number):
            raise TransformError("BOUNDARY_INVALID", "A normal chapter cannot use an SP Number", {"item_index": item_index})
    return patch


@dataclass
class SplitItem:
    index: int
    kind: str
    start: int
    end: int
    output: Path
    identity: dict[str, str | None]
    visual_evidence: dict[str, Any]


def _extract_boundary_items(plan: Any, plan_path: Path, output_dir: Path | None) -> tuple[Mapping[str, Any], list[SplitItem]]:
    if isinstance(plan, list):
        plan_object: Mapping[str, Any] = {"boundaries": plan}
        raw_items = plan
    elif isinstance(plan, dict):
        plan_object = plan
        raw_items = _first_present(plan, ("boundaries", "items", "outputs"))
    else:
        raise TransformError("BOUNDARY_INVALID", "Boundary table must be an object or array")
    if not isinstance(raw_items, list) or not raw_items:
        raise TransformError("BOUNDARY_INVALID", "Boundary table must contain a non-empty item list")
    base = output_dir if output_dir is not None else plan_path.parent
    parsed: list[SplitItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise TransformError("BOUNDARY_INVALID", "Each boundary item must be an object", {"item_index": index})
        raw_kind = _first_present(raw_item, ("kind", "type", "packaging_mode"))
        if not isinstance(raw_kind, str):
            raise TransformError("BOUNDARY_INVALID", "Each boundary item requires kind=chapter or kind=special", {"item_index": index})
        kind = raw_kind.strip().casefold()
        if kind not in {"chapter", "special"}:
            raise TransformError("BOUNDARY_INVALID", "Unsupported boundary item kind", {"item_index": index, "kind": raw_kind})
        if kind == "special":
            special_evidence = raw_item.get("evidence")
            confidence = raw_item.get("confidence")
            if raw_item.get("complete_independent_range") is not True:
                raise TransformError(
                    "SPECIAL_RANGE_INCOMPLETE",
                    "A Special requires an explicitly confirmed complete independent page range",
                    {"item_index": index},
                )
            if not _manifest_confidence_is_high(confidence):
                raise TransformError(
                    "MANIFEST_UNCERTAIN_SOURCE",
                    "A Special split requires explicit high-confidence identity evidence",
                    {"item_index": index, "confidence": confidence},
                )
            if not isinstance(special_evidence, (str, list, dict)) or not special_evidence:
                raise TransformError(
                    "BOUNDARY_INVALID",
                    "A Special split must record the evidence for its independent identity",
                    {"item_index": index},
                )
        start = _required_page_number(raw_item, ("start_page", "start"), index)
        end = _required_page_number(raw_item, ("end_page", "end"), index)
        if end < start:
            raise TransformError("BOUNDARY_INVALID", "Boundary end precedes its start", {"item_index": index, "start": start, "end": end})
        raw_output = _first_present(raw_item, ("output", "output_path"))
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise TransformError("BOUNDARY_INVALID", "Each boundary item requires an output path", {"item_index": index})
        parsed.append(
            SplitItem(
                index=index,
                kind=kind,
                start=start,
                end=end,
                output=_resolved_output(raw_output, base),
                identity=_item_identity(raw_item, kind, index),
                visual_evidence=_visual_evidence(plan_object, raw_item, index),
            )
        )
    return plan_object, parsed


def _validate_chapter_numbers(items: Sequence[SplitItem], require_continuous: bool) -> None:
    chapters = [item for item in items if item.kind == "chapter"]
    if not chapters:
        return
    seen: dict[str, int] = {}
    numeric: list[Decimal] = []
    all_integers = True
    for item in chapters:
        number = item.identity["Number"]
        assert number is not None
        folded = number.casefold()
        if folded in seen:
            raise TransformError(
                "BOUNDARY_CHAPTER_SEQUENCE",
                "Boundary table contains duplicate chapter Numbers",
                {"number": number, "item_indexes": [seen[folded], item.index]},
            )
        seen[folded] = item.index
        try:
            decimal = Decimal(number)
        except InvalidOperation as exc:
            raise TransformError(
                "BOUNDARY_CHAPTER_SEQUENCE",
                "Chapter Numbers in a split table must be numeric",
                {"number": number, "item_index": item.index},
            ) from exc
        numeric.append(decimal)
        all_integers = all_integers and decimal == decimal.to_integral_value()
    if any(current <= previous for previous, current in zip(numeric, numeric[1:])):
        raise TransformError("BOUNDARY_CHAPTER_SEQUENCE", "Chapter Numbers must be strictly increasing in source-page order")
    if require_continuous and all_integers:
        for previous, current in zip(numeric, numeric[1:]):
            if current != previous + 1:
                raise TransformError(
                    "BOUNDARY_CHAPTER_SEQUENCE",
                    "Integer chapter Numbers must be continuous",
                    {"previous": str(previous), "current": str(current)},
                )


def _image_output_name(output_index: int, page_count: int, source_name: str) -> str:
    width = max(4, len(str(page_count)))
    suffix = PurePosixPath(source_name).suffix.casefold()
    return f"{output_index:0{width}d}{suffix}"


def _split(args: argparse.Namespace) -> dict[str, Any]:
    source = _resolved_input(args.source)
    boundary_path = _resolved_input(args.boundaries)
    output_dir = None
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve(strict=False)
    plan = _load_json(boundary_path)
    plan_object, items = _extract_boundary_items(plan, boundary_path, output_dir)
    identity_policy = str(plan_object.get("identity_policy", "continuous-chapter")).strip().casefold().replace("_", "-")
    if identity_policy not in {"continuous-chapter", "volume-aware-chapter"}:
        raise TransformError("BOUNDARY_INVALID", "CBZ splitting requires continuous-chapter or volume-aware-chapter policy")
    for item in items:
        if item.kind == "special":
            item.identity["Volume"] = None
            continue
        if item.kind != "chapter":
            continue
        if identity_policy == "continuous-chapter":
            item.identity["Volume"] = None
            if re.search(r"(?i)\bvol(?:ume)?\.?\s*\d+", item.output.stem):
                raise TransformError(
                    "KAVITA_VOLUME_JUMP_RISK",
                    "continuous-chapter output filename must omit Vol.xx",
                    {"output": str(item.output)},
                )
        elif not item.identity.get("Volume"):
            raise TransformError(
                "BOUNDARY_INVALID",
                "volume-aware-chapter output requires a confirmed ComicInfo Volume",
                {"item_index": item.index},
            )
    declared_source = plan_object.get("source") if isinstance(plan_object, dict) else None
    if declared_source is not None:
        if not isinstance(declared_source, str):
            raise TransformError("BOUNDARY_INVALID", "Boundary plan source must be a path string")
        plan_source = _resolved_input(declared_source, boundary_path.parent)
        if plan_source != source:
            raise TransformError(
                "BOUNDARY_SOURCE_MISMATCH",
                "Boundary plan source does not match --source",
                {"plan_source": str(plan_source), "source": str(source)},
            )
    _check_output_set([item.output for item in items], [source], args.overwrite)
    before = _inspect_archive(source)
    source_identity = _identity_snapshot(before.comicinfo_root)
    pages = before.images
    if not pages:
        raise TransformError("PAGE004", "Source CBZ does not contain any supported image pages", {"source": str(source)})
    declared_count = _first_present(plan_object, ("source_page_count", "page_count"))
    if declared_count is not None and declared_count != len(pages):
        raise TransformError(
            "BOUNDARY_PAGE_COUNT_MISMATCH",
            "Boundary plan page count does not match the source CBZ",
            {"declared": declared_count, "actual": len(pages)},
        )
    expected_start = 1
    for item in items:
        if item.start != expected_start:
            code = "BOUNDARY_OVERLAP" if item.start < expected_start else "BOUNDARY_GAP"
            raise TransformError(
                code,
                "Boundary spans must be contiguous, non-overlapping, and in source order",
                {"item_index": item.index, "expected_start": expected_start, "actual_start": item.start},
            )
        if item.end > len(pages):
            raise TransformError(
                "BOUNDARY_OUT_OF_RANGE",
                "Boundary span exceeds the source page count",
                {"item_index": item.index, "end": item.end, "source_page_count": len(pages)},
            )
        expected_start = item.end + 1
    if expected_start != len(pages) + 1:
        raise TransformError(
            "BOUNDARY_INCOMPLETE_COVERAGE",
            "Boundary spans must cover every source image page exactly once",
            {"covered_through": expected_start - 1, "source_page_count": len(pages)},
        )
    require_continuous = plan_object.get("require_continuous_chapter_numbers", True)
    if not isinstance(require_continuous, bool):
        raise TransformError("BOUNDARY_INVALID", "require_continuous_chapter_numbers must be a boolean")
    _validate_chapter_numbers(items, require_continuous)
    xml_info = before.members[before.comicinfo_index].info
    staged_archives: list[StagedArchive] = []
    output_results: list[dict[str, Any]] = []
    planned_entries: list[list[OutputEntry]] = []
    for item in items:
        span = pages[item.start - 1 : item.end]
        root = copy.deepcopy(before.comicinfo_root)
        identity_patch = dict(item.identity)
        if item.kind == "chapter" and identity_patch.get("Format") is None:
            existing_format = _identity_snapshot(root).get("Format")
            if existing_format == "Special":
                identity_patch["Format"] = None
        _apply_patch(root, identity_patch)
        if item.kind == "chapter" and identity_policy == "continuous-chapter" and source_identity.get("Volume"):
            marker = f"Source volume: {source_identity['Volume']}"
            notes_nodes = [child for child in list(root) if _local_name(child.tag) == "Notes"]
            if len(notes_nodes) > 1:
                raise TransformError("META005", "ComicInfo.xml contains duplicate Notes elements")
            current_notes = (notes_nodes[0].text or "").strip() if notes_nodes else ""
            if marker not in current_notes:
                _set_direct_child(root, "Notes", f"{current_notes}\n{marker}".strip())
        _set_direct_child(root, "PageCount", str(len(span)))
        _remove_pages(root)
        xml_bytes = _serialize_comicinfo(root)
        entries: list[OutputEntry] = []
        mapping: list[dict[str, Any]] = []
        for output_index, member in enumerate(span, start=1):
            source_index = item.start + output_index - 1
            output_member = _image_output_name(output_index, len(span), member.info.filename)
            entries.append(
                OutputEntry(
                    _clone_info(member.info, output_member),
                    source_path=source,
                    source_member=member.info.filename,
                )
            )
            mapping.append(
                {
                    "source_page": source_index,
                    "source_member": member.info.filename,
                    "source_sha256": member.sha256,
                    "output_page": output_index,
                    "output_member": output_member,
                    "output_sha256": member.sha256,
                    "byte_identical": True,
                }
            )
        entries.append(OutputEntry(_clone_info(xml_info, "ComicInfo.xml"), data=xml_bytes))
        output_result: dict[str, Any] = {
            "item_index": item.index,
            "kind": item.kind,
            "output": str(item.output),
            "start_page": item.start,
            "end_page": item.end,
            "page_count": len(span),
            "identity": _identity_snapshot(root),
            "visual_evidence": item.visual_evidence,
            "page_mapping": mapping,
            "written": False,
        }
        planned_entries.append(entries)
        output_results.append(output_result)
    if not args.dry_run:
        try:
            for item, entries, output_result in zip(items, planned_entries, output_results):
                staged = _stage_archive(item.output, entries, before.comment)
                staged_archives.append(staged)
                staged_images = staged.snapshot.images
                mapping = output_result["page_mapping"]
                if len(staged_images) != len(mapping):
                    raise TransformError("PDF004", "Staged split page count does not match its planned span", {"output": str(item.output)})
                for map_record, output_member in zip(mapping, staged_images):
                    if map_record["output_member"] != output_member.info.filename or map_record["source_sha256"] != output_member.sha256:
                        raise TransformError(
                            "ARC010",
                            "A staged split page is not byte-identical to its source page",
                            {"output": str(item.output), "member": output_member.info.filename},
                        )
                output_result["output_sha256"] = staged.snapshot.archive_sha256
            _commit_staged(staged_archives, args.overwrite)
        except BaseException:
            for staged in staged_archives:
                try:
                    staged.temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        for output_result in output_results:
            output_result["written"] = True
    return {
        "ok": True,
        "command": "split",
        "dry_run": bool(args.dry_run),
        "source": str(source),
        "source_sha256": before.archive_sha256,
        "source_page_count": len(pages),
        "coverage": {
            "continuous": True,
            "non_overlapping": True,
            "exact": True,
            "covered_pages": len(pages),
        },
        "outputs": output_results,
        "ignored_non_image_members": [
            member.info.filename
            for index, member in enumerate(before.members)
            if index != before.comicinfo_index and not member.info.is_dir() and not member.is_image
        ],
        "source_unchanged": _source_unchanged(before),
    }


def _manifest_confidence_is_high(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and float(value) >= 0.9
    if isinstance(value, str):
        return value.strip().casefold() in {"high", "verified", "confirmed"}
    return False


def _load_pillow() -> tuple[Any | None, str | None]:
    try:
        from PIL import Image
    except (ImportError, OSError) as exc:
        return None, str(exc)
    return Image, None


def _read_member_bytes(path: Path, member_name: str) -> bytes:
    try:
        with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
            return archive.read(member_name)
    except (OSError, RuntimeError, KeyError, zipfile.BadZipFile) as exc:
        raise TransformError(
            "ARC002",
            "Could not read an image page from a validated source",
            {"source": str(path), "member": member_name, "reason": str(exc)},
        ) from exc


def _dhash_image(Image: Any, image_bytes: bytes, source: Path, member: str) -> tuple[str, tuple[int, int]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                dimensions = (int(image.width), int(image.height))
                if dimensions[0] < 1 or dimensions[1] < 1:
                    raise ValueError("image has zero dimensions")
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                rgb = image.convert("RGB").resize((8, 8), resampling)
                rgb_pixels = list(
                    rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata()
                )
                grayscale = image.convert("L").resize((9, 8), resampling)
                pixels = list(
                    grayscale.get_flattened_data()
                    if hasattr(grayscale, "get_flattened_data")
                    else grayscale.getdata()
                )
    except Exception as exc:
        raise TransformError(
            "PAGE004",
            "Image page could not be decoded for perceptual hashing",
            {"source": str(source), "member": member, "reason": str(exc)},
        ) from exc
    difference_bits = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            difference_bits = (difference_bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    pixel_count = len(rgb_pixels)
    averages = tuple(sum(pixel[channel] for pixel in rgb_pixels) // pixel_count for channel in range(3))
    color_bits = (averages[0] << 16) | (averages[1] << 8) | averages[2]
    combined_bits = (difference_bits << 24) | color_bits
    return f"{combined_bits:022x}", dimensions


def _hamming_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _similar_aspect(left: tuple[int, int], right: tuple[int, int]) -> bool:
    left_cross = left[0] * right[1]
    right_cross = right[0] * left[1]
    scale = max(left_cross, right_cross)
    return scale > 0 and abs(left_cross - right_cross) / scale <= 0.01


@dataclass
class KeptPage:
    output_index: int
    output_member: str
    source_path: Path
    source_member: MemberSnapshot
    source_page: int
    byte_sha256: str
    perceptual_hash: str | None
    dimensions: tuple[int, int] | None


@dataclass
class ManifestSource:
    index: int
    path: Path
    official_evidence: Any
    confidence_evidence: Any
    snapshot: ArchiveSnapshot


def _manifest_sources(manifest: Mapping[str, Any], manifest_path: Path) -> list[ManifestSource]:
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise TransformError("MANIFEST_INVALID", "Special manifest requires a non-empty sources array")
    result: list[ManifestSource] = []
    seen: set[Path] = set()
    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            raise TransformError(
                "MANIFEST_UNTRUSTED_SOURCE",
                "Every Special source must be an object with explicit trust evidence",
                {"source_index": index},
            )
        path_value = _first_present(raw_source, ("path", "source"))
        if not isinstance(path_value, str) or not path_value.strip():
            raise TransformError("MANIFEST_INVALID", "Manifest source requires a path", {"source_index": index})
        official = raw_source.get("official")
        confidence = raw_source.get("confidence")
        if official is not True:
            raise TransformError(
                "MANIFEST_UNOFFICIAL_SOURCE",
                "Special merging refuses unofficial or unverified source items",
                {"source_index": index, "official": official},
            )
        if not _manifest_confidence_is_high(confidence):
            raise TransformError(
                "MANIFEST_UNCERTAIN_SOURCE",
                "Special merging requires explicit high confidence for every source item",
                {"source_index": index, "confidence": confidence},
            )
        path = _resolved_input(path_value, manifest_path.parent)
        if path in seen:
            raise TransformError("MANIFEST_INVALID", "Manifest lists the same source archive more than once", {"path": str(path)})
        seen.add(path)
        snapshot = _inspect_archive(path)
        if not snapshot.images:
            raise TransformError("PAGE004", "Special source contains no supported image pages", {"source": str(path)})
        result.append(ManifestSource(index, path, official, confidence, snapshot))
    return result


def _visual_review_decisions(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = manifest.get("visual_review", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TransformError("MANIFEST_INVALID", "visual_review must be an object keyed by source_index:page")
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not re.fullmatch(r"\d+:[1-9]\d*", key):
            raise TransformError("MANIFEST_INVALID", "visual_review keys must use source_index:page", {"key": key})
        if not isinstance(value, dict) or not isinstance(value.get("duplicate"), bool):
            raise TransformError("MANIFEST_INVALID", "visual_review entries require boolean duplicate", {"key": key})
        reason = value.get("reason")
        reviewer = value.get("reviewer")
        if not isinstance(reason, str) or not reason.strip() or not isinstance(reviewer, str) or not reviewer.strip():
            raise TransformError(
                "MANIFEST_INVALID",
                "visual_review entries require non-empty reason and reviewer",
                {"key": key},
            )
        bound_hashes: dict[str, str] = {}
        for field in ("source_archive_sha256", "source_page_sha256", "duplicate_target_sha256"):
            digest = value.get(field)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise TransformError(
                    "MANIFEST_INVALID",
                    "visual_review decisions must bind source archive, source page, and duplicate target SHA-256",
                    {"key": key, "field": field},
                )
            bound_hashes[field] = digest.casefold()
        result[key] = {
            "duplicate": value["duplicate"],
            "reason": reason.strip(),
            "reviewer": reviewer.strip(),
            **bound_hashes,
        }
    return result


def _special_identity(manifest: Mapping[str, Any]) -> dict[str, str | None]:
    raw_identity = manifest.get("identity", {})
    if not isinstance(raw_identity, dict):
        raise TransformError("MANIFEST_INVALID", "Manifest identity must be an object")
    patch = _canonical_patch(raw_identity) if raw_identity else {}
    supplied_number = _first_present(manifest, ("number", "special_number"))
    if supplied_number is not None:
        if isinstance(supplied_number, bool) or not isinstance(supplied_number, (str, int)):
            raise TransformError("MANIFEST_INVALID", "Special number must be a string or integer")
        supplied_text = str(supplied_number)
        if "Number" in patch and patch["Number"] != supplied_text:
            raise TransformError("MANIFEST_INVALID", "Manifest number conflicts with identity.Number")
        patch["Number"] = supplied_text
    number = patch.get("Number")
    if not number or not SPECIAL_NUMBER_RE.fullmatch(number):
        raise TransformError("MANIFEST_INVALID", "Merged Special Number must use SP followed by digits", {"number": number})
    if patch.get("Format") not in (None, "Special"):
        raise TransformError("MANIFEST_INVALID", "Merged Special Format must be Special")
    patch["Number"] = number.upper()
    patch["Format"] = "Special"
    patch["Volume"] = None
    title = patch.get("Title")
    if not title or re.search(r"[\u3400-\u9fff]", title) is None:
        raise TransformError("META008", "Merged Special requires an explicit reliable Chinese Title")
    return patch


def _merge_specials(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = _resolved_input(args.manifest)
    raw_manifest = _load_json(manifest_path)
    if not isinstance(raw_manifest, dict):
        raise TransformError("MANIFEST_INVALID", "Special manifest must be a JSON object")
    manifest: Mapping[str, Any] = raw_manifest
    output_value = args.output if args.output else manifest.get("output")
    if not isinstance(output_value, str) or not output_value.strip():
        raise TransformError("MANIFEST_INVALID", "Merged Special requires --output or manifest.output")
    output = _resolved_output(output_value, manifest_path.parent)
    sources = _manifest_sources(manifest, manifest_path)
    _check_output_set([output], [source.path for source in sources], args.overwrite)
    identity_patch = _special_identity(manifest)
    manifest_distance = manifest.get("perceptual_distance", 4)
    distance = args.phash_distance if args.phash_distance is not None else manifest_distance
    if isinstance(distance, bool) or not isinstance(distance, int) or not 0 <= distance <= 16:
        raise TransformError("MANIFEST_INVALID", "Perceptual hash distance must be an integer from 0 through 16")
    Image, pillow_reason = _load_pillow()
    if Image is None:
        raise TransformError(
            "PILLOW_REQUIRED",
            "Special merge requires Pillow so every page can be decoded and perceptually hashed",
            {"reason": pillow_reason},
        )
    visual_decisions = _visual_review_decisions(manifest)
    exact_targets: dict[str, KeptPage] = {}
    kept: list[KeptPage] = []
    omitted: list[dict[str, Any]] = []
    unresolved_visual_reviews: list[dict[str, Any]] = []
    confirmed_unique_reviews: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    for source in sources:
        source_reports.append(
            {
                "source_index": source.index,
                "path": str(source.path),
                "archive_sha256": source.snapshot.archive_sha256,
                "official": source.official_evidence,
                "confidence": source.confidence_evidence,
                "page_count": len(source.snapshot.images),
                "source_preserved": None,
            }
        )
        for source_page, member in enumerate(source.snapshot.images, start=1):
            exact_target = exact_targets.get(member.sha256)
            if exact_target is not None:
                omitted.append(
                    {
                        "source_index": source.index,
                        "source": str(source.path),
                        "source_page": source_page,
                        "source_member": member.info.filename,
                        "byte_sha256": member.sha256,
                        "dedupe_target": {
                            "output_page": exact_target.output_index,
                            "output_member": exact_target.output_member,
                            "source": str(exact_target.source_path),
                            "source_page": exact_target.source_page,
                            "source_member": exact_target.source_member.info.filename,
                        },
                        "evidence": {"method": "byte-sha256", "sha256": member.sha256},
                    }
                )
                continue
            perceptual_hash: str | None = None
            dimensions: tuple[int, int] | None = None
            perceptual_target: KeptPage | None = None
            perceptual_distance: int | None = None
            if Image is not None:
                image_bytes = _read_member_bytes(source.path, member.info.filename)
                perceptual_hash, dimensions = _dhash_image(Image, image_bytes, source.path, member.info.filename)
                for candidate in kept:
                    if candidate.perceptual_hash is None or candidate.dimensions is None:
                        continue
                    candidate_distance = _hamming_hex(perceptual_hash, candidate.perceptual_hash)
                    if candidate_distance <= distance and _similar_aspect(dimensions, candidate.dimensions):
                        if perceptual_distance is None or candidate_distance < perceptual_distance:
                            perceptual_target = candidate
                            perceptual_distance = candidate_distance
                if perceptual_target is not None:
                    review_key = f"{source.index}:{source_page}"
                    review = visual_decisions.get(review_key)
                    candidate_record = {
                        "source_index": source.index,
                        "source": str(source.path),
                        "source_page": source_page,
                        "source_member": member.info.filename,
                        "byte_sha256": member.sha256,
                        "source_archive_sha256": source.snapshot.archive_sha256,
                        "perceptual_hash": perceptual_hash,
                        "dimensions": list(dimensions),
                        "dedupe_target": {
                            "output_page": perceptual_target.output_index,
                            "output_member": perceptual_target.output_member,
                            "source": str(perceptual_target.source_path),
                            "source_page": perceptual_target.source_page,
                            "source_member": perceptual_target.source_member.info.filename,
                            "byte_sha256": perceptual_target.byte_sha256,
                            "perceptual_hash": perceptual_target.perceptual_hash,
                            "dimensions": list(perceptual_target.dimensions or ()),
                        },
                        "perceptual_evidence": {
                            "method": "dhash-88-color",
                            "hamming_distance": perceptual_distance,
                            "threshold": distance,
                            "aspect_ratio_within_one_percent": True,
                        },
                        "visual_review_key": review_key,
                    }
                    if review is None:
                        unresolved_visual_reviews.append(candidate_record)
                    elif (
                        review["source_archive_sha256"] != source.snapshot.archive_sha256
                        or review["source_page_sha256"] != member.sha256
                        or review["duplicate_target_sha256"] != perceptual_target.byte_sha256
                    ):
                        raise TransformError(
                            "SPECIAL_VISUAL_REVIEW_STALE",
                            "A perceptual-duplicate decision no longer matches its source and target page hashes",
                            {"visual_review_key": review_key, "candidate": candidate_record},
                        )
                    elif review["duplicate"]:
                        omitted_record = dict(candidate_record)
                        omitted_record["evidence"] = {
                            **candidate_record["perceptual_evidence"],
                            "visual_review": review,
                        }
                        omitted.append(omitted_record)
                        continue
                    else:
                        unique_record = dict(candidate_record)
                        unique_record["visual_review"] = review
                        confirmed_unique_reviews.append(unique_record)
            output_index = len(kept) + 1
            output_member = _image_output_name(output_index, sum(len(item.snapshot.images) for item in sources), member.info.filename)
            kept_page = KeptPage(
                output_index,
                output_member,
                source.path,
                member,
                source_page,
                member.sha256,
                perceptual_hash,
                dimensions,
            )
            kept.append(kept_page)
            exact_targets[member.sha256] = kept_page
    if not kept:
        raise TransformError("MANIFEST_INVALID", "Special merge did not retain any unique pages")
    root = copy.deepcopy(sources[0].snapshot.comicinfo_root)
    _apply_patch(root, identity_patch)
    _set_direct_child(root, "PageCount", str(len(kept)))
    _remove_pages(root)
    xml_bytes = _serialize_comicinfo(root)
    xml_info = sources[0].snapshot.members[sources[0].snapshot.comicinfo_index].info
    entries = [
        OutputEntry(
            _clone_info(page.source_member.info, page.output_member),
            source_path=page.source_path,
            source_member=page.source_member.info.filename,
        )
        for page in kept
    ]
    entries.append(OutputEntry(_clone_info(xml_info, "ComicInfo.xml"), data=xml_bytes))
    kept_report = [
        {
            "output_page": page.output_index,
            "output_member": page.output_member,
            "source": str(page.source_path),
            "source_page": page.source_page,
            "source_member": page.source_member.info.filename,
            "byte_sha256": page.byte_sha256,
            "perceptual_hash": page.perceptual_hash,
            "dimensions": list(page.dimensions) if page.dimensions else None,
        }
        for page in kept
    ]
    result: dict[str, Any] = {
        "ok": True,
        "command": "merge-specials",
        "dry_run": bool(args.dry_run),
        "output": str(output),
        "identity": _identity_snapshot(root),
        "sources": source_reports,
        "input_page_count": sum(len(item.snapshot.images) for item in sources),
        "output_page_count": len(kept),
        "kept_pages": kept_report,
        "omitted_pages": omitted,
        "deduplication": {
            "byte_sha256": True,
            "perceptual_hash": {
                "available": True,
                "algorithm": "dhash-88-color",
                "distance_threshold": distance,
                "unavailable_reason": None,
            },
            "unresolved_visual_reviews": unresolved_visual_reviews,
            "confirmed_unique_reviews": confirmed_unique_reviews,
            "omitted_count": len(omitted),
        },
        "source_unchanged": None,
        "written": False,
    }
    if args.dry_run:
        unchanged = True
        for source, report in zip(sources, source_reports):
            preserved = _source_unchanged(source.snapshot)
            report["source_preserved"] = preserved
            unchanged = unchanged and preserved
        result["source_unchanged"] = unchanged
        return result
    if unresolved_visual_reviews:
        raise TransformError(
            "SPECIAL_VISUAL_REVIEW_REQUIRED",
            "Perceptual duplicate candidates require recorded visual decisions before writing",
            {"candidates": unresolved_visual_reviews},
        )
    staged = _stage_archive(output, entries, sources[0].snapshot.comment)
    staged_images = staged.snapshot.images
    if len(staged_images) != len(kept):
        staged.temporary.unlink(missing_ok=True)
        raise TransformError("PDF004", "Staged Special page count does not match the deduplicated page plan")
    for expected, actual in zip(kept, staged_images):
        if expected.output_member != actual.info.filename or expected.byte_sha256 != actual.sha256:
            staged.temporary.unlink(missing_ok=True)
            raise TransformError(
                "ARC010",
                "A staged Special page is not byte-identical to its selected source page",
                {"member": actual.info.filename},
            )
    final_identity = _identity_snapshot(staged.snapshot.comicinfo_root)
    if final_identity.get("Number") != identity_patch["Number"] or final_identity.get("Format") != "Special":
        staged.temporary.unlink(missing_ok=True)
        raise TransformError("ARC010", "Staged Special ComicInfo identity is invalid")
    _commit_staged([staged], args.overwrite)
    unchanged = True
    for source, report in zip(sources, source_reports):
        preserved = _source_unchanged(source.snapshot)
        report["source_preserved"] = preserved
        unchanged = unchanged and preserved
    result.update(
        {
            "output_sha256": staged.snapshot.archive_sha256,
            "source_unchanged": unchanged,
            "written": True,
        }
    )
    return result


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        description="Perform explicit CBZ transformations and emit one JSON result.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    split_parser = subparsers.add_parser(
        "split",
        help="Split one CBZ from a fully verified JSON boundary table.",
        description=(
            "Boundary JSON may be an array or an object with boundaries/items/outputs. "
            "Each item needs kind, start_page, end_page, output, Number identity, and "
            "affirmative visual review flags. Relative outputs resolve beside the plan "
            "unless --output-dir is supplied."
        ),
    )
    split_parser.add_argument("--source", required=True, help="Source CBZ path.")
    split_parser.add_argument("--boundaries", required=True, help="UTF-8 JSON boundary table path.")
    split_parser.add_argument("--output-dir", help="Base directory for relative boundary output paths.")
    split_parser.add_argument("--dry-run", action="store_true", help="Validate and report without creating output files.")
    split_parser.add_argument("--overwrite", action="store_true", help="Allow atomic replacement of explicitly named outputs.")
    split_parser.set_defaults(handler=_split)

    normalize_parser = subparsers.add_parser(
        "normalize",
        help="Apply an explicit ComicInfo identity patch without changing other members.",
    )
    normalize_parser.add_argument("--source", required=True, help="Source CBZ path.")
    normalize_parser.add_argument("--output", required=True, help="Candidate output CBZ path; it must differ from source.")
    normalize_parser.add_argument("--patch", required=True, help="UTF-8 JSON identity patch path.")
    normalize_parser.add_argument("--dry-run", action="store_true", help="Validate and report without creating the output.")
    normalize_parser.add_argument("--overwrite", action="store_true", help="Allow atomic replacement of the explicit output.")
    normalize_parser.set_defaults(handler=_normalize)

    merge_parser = subparsers.add_parser(
        "merge-specials",
        help="Merge official high-confidence CBZ sources into one deduplicated Special.",
    )
    merge_parser.add_argument("--manifest", required=True, help="UTF-8 JSON source and identity manifest path.")
    merge_parser.add_argument("--output", help="Output CBZ path; overrides manifest.output.")
    merge_parser.add_argument(
        "--phash-distance",
        type=int,
        help="Maximum dHash Hamming distance (0-16); overrides manifest.perceptual_distance.",
    )
    merge_parser.add_argument("--dry-run", action="store_true", help="Validate and report without creating the output.")
    merge_parser.add_argument("--overwrite", action="store_true", help="Allow atomic replacement of the explicit output.")
    merge_parser.set_defaults(handler=_merge_specials)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        result = args.handler(args)
        _emit(result)
        return 0
    except CleanParserExit as exc:
        return exc.status
    except TransformError as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        )
        return 2
    except KeyboardInterrupt:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "INTERRUPTED",
                    "message": "Operation was interrupted before completion",
                    "details": {},
                },
            }
        )
        return 130
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Unexpected transformation failure",
                    "details": {"type": type(exc).__name__, "reason": str(exc)},
                },
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
