#!/usr/bin/env python3
"""Validate, checkpoint, resume, and safely promote manga libraries.

The command is deliberately conservative:

* every mutating command is a dry run unless ``--execute`` is supplied;
* an existing checkpoint is never replaced without ``--update``;
* checkpoint files are written beside their destination and installed with
  :func:`os.replace`;
* a promotion validates the candidate before any rename and validates the
  formal path again afterwards;
* failed promotions move the failed candidate out of the formal path before
  restoring the previous library.

All command results, including failures, are emitted as one JSON object on
stdout.  The plan, baseline, and resume-state readers accept either list-style
records (``[{"path": ..., "sha256": ...}]``) or path-to-hash maps.  This keeps
the safety tool independent from a particular report renderer.
"""

from __future__ import print_function

import argparse
import copy
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET

try:
    from PIL import Image as PILImage  # type: ignore
except ImportError:
    PILImage = None


ARCHIVE_SUFFIXES = {".cbz", ".zip"}
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"
}
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
CHAPTER_TOKEN_RE = re.compile(r"(?i)(?:^|[\s._-])ch(?:apter)?[\s._-]*([0-9]+(?:\.[0-9]+)?)")
VOLUME_TOKEN_RE = re.compile(r"(?i)(?:^|[\s._-])vol(?:ume)?[\s._-]*[0-9]+|(?:^|[\s._-])v[0-9]+")
VOLUME_VALUE_RE = re.compile(r"(?i)(?:^|[\s._-])(?:vol(?:ume)?|v)[\s._-]*([0-9]+(?:\.[0-9]+)?)")
SPECIAL_TOKEN_RE = re.compile(r"(?i)^SP[\s._-]*([0-9]+(?:\.[0-9]+)?)$")
SPECIAL_FILENAME_RE = re.compile(r"(?i)(?:^|[\s._-])SP[\s._-]*([0-9]+(?:\.[0-9]+)?)")
ALLOWED_IDENTITY_POLICIES = {"continuous-chapter", "volume-aware-chapter", "volume-only"}
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBER_EXPANSION_RATIO = 1_000.0
DEFAULT_PERCEPTUAL_DISTANCE = 4


class CliUsageError(Exception):
    """Raised instead of letting argparse print a non-JSON error."""


class CleanParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class JsonArgumentParser(argparse.ArgumentParser):
    def print_help(self, file: Any = None) -> None:
        _json_print({"ok": True, "kind": "help", "prog": self.prog, "help": self.format_help()})

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if message:
            raise CliUsageError(message.strip())
        raise CleanParserExit(status)

    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _issue(code: str, message: str, path: Optional[Any] = None, **details: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        item["path"] = str(path)
    if details:
        item["details"] = details
    return item


def _json_print(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _read_json_file(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ValueError("{} JSON does not exist: {}".format(label, path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read {} JSON {}: {}".format(label, path, exc))


def _read_json_value(value: str, label: str, base: Optional[Path] = None) -> Any:
    """Read inline JSON, ``@file`` JSON, or a plainly named JSON file."""
    candidate = value
    if value.startswith("@"):
        path = Path(value[1:])
        if base is not None and not path.is_absolute():
            path = base / path
        return _read_json_file(path, label)
    stripped = value.lstrip()
    if stripped.startswith("{") or stripped.startswith("[") or stripped in {"null", "true", "false"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid inline {} JSON: {}".format(label, exc))
    path = Path(candidate)
    if base is not None and not path.is_absolute():
        path = base / path
    return _read_json_file(path, label)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> Dict[str, str]:
    manifest: Dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if path.is_symlink():
            raise ValueError("tree manifest refuses symlink: {}".format(path))
        if path.is_file():
            manifest[path.relative_to(root).as_posix()] = _sha256(path)
    return manifest


def _tree_safety_errors(root: Path) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold())
    except OSError as exc:
        return [_issue("library_tree_scan_failed", str(exc), root)]
    for path in entries:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(_issue("library_tree_symlink", "library trees must not contain symbolic links", relative))
            continue
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            errors.append(_issue("library_tree_stat_failed", str(exc), relative))
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            errors.append(_issue("library_tree_special_file", "library trees may contain only regular files and directories", relative))
    return errors


def _validate_checksums_manifest(library: Path, required: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    manifest_path = library / "_reports" / "checksums.sha256"
    result: Dict[str, Any] = {"path": str(manifest_path), "present": manifest_path.is_file(), "checked": 0}
    if not manifest_path.exists():
        issue = _issue("checksums_manifest_missing", "_reports/checksums.sha256 is required for final promotion", manifest_path)
        (errors if required else warnings).append(issue)
        return result, errors, warnings
    if manifest_path.is_symlink() or not manifest_path.is_file():
        errors.append(_issue("checksums_manifest_unsafe", "checksums manifest must be a regular non-symlink file", manifest_path))
        return result, errors, warnings
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(_issue("checksums_manifest_read_failed", str(exc), manifest_path))
        return result, errors, warnings
    seen: Set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?(.+)", line)
        if match is None:
            errors.append(_issue("checksums_manifest_invalid", "invalid SHA-256 manifest line", manifest_path, line=line_number))
            continue
        expected, raw = match.group(1).casefold(), match.group(2)
        safe = _safe_relative(raw)
        if safe is None:
            errors.append(_issue("checksums_manifest_unsafe_path", "manifest entry is not a safe relative path", manifest_path, line=line_number, entry=raw))
            continue
        normalized = safe.as_posix()
        if normalized in seen:
            errors.append(_issue("checksums_manifest_duplicate", "manifest lists a path more than once", manifest_path, entry=normalized))
            continue
        seen.add(normalized)
        target = library / safe
        if target.is_symlink() or not target.is_file():
            errors.append(_issue("checksums_target_missing", "manifest target is missing or unsafe", target))
            continue
        actual = _sha256(target)
        result["checked"] += 1
        if actual != expected:
            errors.append(_issue("checksums_mismatch", "manifest SHA-256 differs from target", target, expected=expected, actual=actual))
    if result["checked"] == 0:
        errors.append(_issue("checksums_manifest_empty", "checksums manifest contains no verifiable entries", manifest_path))
    archive_paths = {path.relative_to(library).as_posix() for path in _archive_paths(library)}
    missing_archives = sorted(archive_paths - seen)
    extra_archives = sorted((seen & {item for item in seen if PurePosixPath(item).suffix.casefold() in ARCHIVE_SUFFIXES}) - archive_paths)
    if missing_archives:
        errors.append(_issue("checksums_archive_coverage_missing", "checksums manifest does not cover every CBZ/ZIP archive", manifest_path, archives=missing_archives))
    if extra_archives:
        errors.append(_issue("checksums_archive_coverage_extra", "checksums manifest names archive paths outside the validated library set", manifest_path, archives=extra_archives))
    result["entries"] = sorted(seen)
    return result, errors, warnings


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


def _locator_key(path: Path) -> str:
    """Canonical comparison key after the path itself has passed symlink checks."""
    return os.path.realpath(os.path.abspath(str(path)))


def _safe_relative(raw: str) -> Optional[Path]:
    text = str(raw).replace("\\", "/")
    if "\x00" in text:
        return None
    pure = PurePosixPath(text)
    if not text or text.startswith("/") or DRIVE_RE.match(text):
        return None
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    return Path(*pure.parts)


def _natural_key(value: str) -> Tuple[Any, ...]:
    parts = re.split(r"([0-9]+(?:\.[0-9]+)?)", value.casefold())
    result: List[Any] = []
    for part in parts:
        if not part:
            continue
        try:
            result.append((0, Decimal(part)))
        except InvalidOperation:
            result.append((1, part))
    return tuple(result)


def _walk_dicts(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _xml_text(root: ET.Element, name: str) -> Optional[str]:
    for child in root.iter():
        local = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if local.casefold() == name.casefold():
            text = (child.text or "").strip()
            return text or None
    return None


def _decimal_identity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite() or number < 0:
        return None
    rendered = format(number.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _series_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _archive_canonical_identity(archive: Mapping[str, Any]) -> str:
    number = str(archive.get("number") or "")
    volume = str(archive.get("volume") or "")
    if str(archive.get("format") or "").casefold() == "special" or SPECIAL_TOKEN_RE.match(number):
        return "special:{}".format(number)
    if number:
        return "chapter:{}".format(number)
    return "volume:{}".format(volume)


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _archive_paths(library: Path) -> List[Path]:
    return sorted(
        (path for path in library.rglob("*") if path.is_file() and path.suffix.casefold() in ARCHIVE_SUFFIXES),
        key=lambda item: item.relative_to(library).as_posix().casefold(),
    )


def _plan_policies(plan: Optional[Any]) -> Tuple[str, Dict[str, str]]:
    default = "continuous-chapter"
    by_series: Dict[str, str] = {}
    if not isinstance(plan, (Mapping, list)):
        return default, by_series
    if isinstance(plan, Mapping):
        raw_default = plan.get("identity_policy")
        if isinstance(raw_default, str):
            default = raw_default
    for record in _walk_dicts(plan):
        series = _first(record, ("series", "Series", "series_name"))
        policy = _first(record, ("identity_policy", "series_identity_policy"))
        if isinstance(series, str) and isinstance(policy, str):
            by_series[_series_key(series)] = policy
    return default, by_series


def _policy_errors(plan: Optional[Any]) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    if plan is None:
        return errors
    seen: Dict[str, Set[str]] = defaultdict(set)
    if isinstance(plan, Mapping) and isinstance(plan.get("identity_policy"), str):
        default = str(plan["identity_policy"]).casefold().replace("_", "-")
        if default not in ALLOWED_IDENTITY_POLICIES:
            errors.append(_issue("unknown_identity_policy", "plan selects an unsupported identity policy", policy=default))
    for record in _walk_dicts(plan):
        series = _first(record, ("series", "Series", "series_name"))
        policy = _first(record, ("identity_policy", "series_identity_policy"))
        if not isinstance(policy, str):
            continue
        normalized = policy.casefold().replace("_", "-")
        if normalized not in ALLOWED_IDENTITY_POLICIES:
            errors.append(_issue("unknown_identity_policy", "series selects an unsupported identity policy", series, policy=normalized))
        if isinstance(series, str):
            seen[_series_key(series)].add(normalized)
    for key, policies in sorted(seen.items()):
        if len(policies) > 1:
            errors.append(_issue("SERIES_IDENTITY_MIX", "plan assigns conflicting identity policies to one series", key, policies=sorted(policies)))
    return errors


def _fallback_documents(plan: Optional[Any]) -> List[Mapping[str, Any]]:
    if plan is None:
        return []
    return [
        record for record in _walk_dicts(plan)
        if str(record.get("packaging_mode", "")).casefold().replace("_", "-") == "volume-fallback"
    ]


def _special_documents(plan: Optional[Any]) -> List[Mapping[str, Any]]:
    if plan is None:
        return []
    return [
        record for record in _walk_dicts(plan)
        if str(record.get("kind", record.get("packaging_mode", ""))).casefold().replace("_", "-")
        in {"special", "merged-special"}
    ]


def _match_special(documents: Sequence[Mapping[str, Any]], relative: str, series: str, number: str) -> Optional[Mapping[str, Any]]:
    best: Optional[Mapping[str, Any]] = None
    best_score = -1
    for record in documents:
        score = 0
        raw_path = _record_path(record)
        if raw_path:
            normalized = raw_path.lstrip("./")
            if normalized == relative:
                score += 100
            elif PurePosixPath(normalized).name == PurePosixPath(relative).name:
                score += 50
        doc_series = _first(record, ("series", "Series", "series_name"))
        if isinstance(doc_series, str) and _series_key(doc_series) == _series_key(series):
            score += 10
        doc_number = _first(record, ("special", "number", "Number"))
        if isinstance(doc_number, str) and doc_number.casefold().replace(" ", "") == number.casefold().replace(" ", ""):
            score += 10
        if score > best_score and score >= 20:
            best, best_score = record, score
    return best


def _high_confidence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) >= 0.9
    return isinstance(value, str) and value.strip().casefold() in {"high", "verified", "confirmed"}


def _special_document_errors(
    document: Mapping[str, Any],
    actual_page_count: int,
    actual_page_hashes: Sequence[str],
    plan_base: Path,
) -> List[str]:
    problems: List[str] = []
    if document.get("complete_independent_range") is not True:
        problems.append("complete independent Special range is not confirmed")
    if not _high_confidence(_first(document, ("identity_confidence", "confidence"))):
        problems.append("Special identity confidence is not high")
    evidence = document.get("evidence")
    if not isinstance(evidence, (list, Mapping, str)) or not evidence:
        problems.append("Special identity evidence is missing")
    try:
        documented_count = int(document.get("page_count"))
    except (TypeError, ValueError):
        documented_count = -1
    if documented_count != actual_page_count:
        problems.append("Special documented page count differs from the archive")
    if document.get("deduplication_status") not in {"complete", "not-applicable"}:
        problems.append("Special deduplication audit is incomplete")
    if not isinstance(document.get("audit_record_id"), str) or not document.get("audit_record_id", "").strip():
        problems.append("Special audit record ID is missing")
    components = document.get("source_components")
    component_hashes: Dict[str, str] = {}
    component_pages: Dict[str, List[Dict[str, Any]]] = {}
    component_included: Dict[str, List[int]] = {}
    if not isinstance(components, list) or not components:
        problems.append("Special source components are missing")
    else:
        for component in components:
            if not isinstance(component, Mapping):
                problems.append("Special source component is invalid")
                continue
            raw = component.get("source")
            digest = component.get("source_sha256")
            included = component.get("included_source_pages")
            if not isinstance(raw, str) or not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
                problems.append("Special source component lacks path or SHA-256")
                continue
            path = Path(raw)
            if not path.is_absolute():
                safe = _safe_relative(raw)
                if safe is None:
                    problems.append("Special source component path is unsafe")
                    continue
                path = plan_base / safe
            path = _absolute(path)
            if path.is_symlink() or not path.is_file() or _sha256(path) != digest.casefold():
                problems.append("Special source component hash does not match its preserved file")
            else:
                page_evidence, page_error = _verified_source_page_evidence(path)
                if page_error is not None or page_evidence is None:
                    problems.append("Special source component pages cannot be independently verified")
                else:
                    component_pages[str(path)] = page_evidence
            if not isinstance(included, list) or not all(isinstance(page, int) and not isinstance(page, bool) and page > 0 for page in included):
                problems.append("Special source component included-page list is invalid")
            else:
                component_included[str(path)] = list(included)
            component_hashes[str(path)] = digest.casefold()
    mapping = document.get("output_page_mapping")
    if not isinstance(mapping, list) or len(mapping) != actual_page_count:
        problems.append("Special output page mapping does not match PageCount")
    else:
        output_numbers: List[int] = []
        mapped_hashes: List[str] = []
        mapped_source_pages: Dict[str, List[int]] = defaultdict(list)
        seen_source_pages: Set[Tuple[str, int]] = set()
        for row in mapping:
            if not isinstance(row, Mapping):
                problems.append("Special output page mapping row is invalid")
                continue
            output_page = row.get("output_page")
            source = row.get("source")
            source_page = row.get("source_page")
            source_archive_sha256 = row.get("source_archive_sha256")
            output_sha256 = row.get("output_sha256")
            if not isinstance(output_page, int) or isinstance(output_page, bool):
                problems.append("Special output page number is invalid")
                continue
            output_numbers.append(output_page)
            if not isinstance(source_page, int) or isinstance(source_page, bool) or source_page < 1:
                problems.append("Special source page number is invalid")
            if not isinstance(source, str):
                problems.append("Special page mapping source is missing")
            else:
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = plan_base / source_path
                source_path = _absolute(source_path)
                if component_hashes.get(str(source_path)) != str(source_archive_sha256).casefold():
                    problems.append("Special page mapping is not bound to a verified source component")
                elif isinstance(source_page, int) and not isinstance(source_page, bool):
                    pages = component_pages.get(str(source_path), [])
                    if source_page < 1 or source_page > len(pages):
                        problems.append("Special page mapping source page is outside the verified source archive")
                    else:
                        source_key = (str(source_path), source_page)
                        if source_key in seen_source_pages:
                            problems.append("Special page mapping reuses one source page more than once")
                        seen_source_pages.add(source_key)
                        mapped_source_pages[str(source_path)].append(source_page)
                        actual_source_hash = pages[source_page - 1]["sha256"]
                        recorded_source_hash = row.get("source_page_sha256")
                        if recorded_source_hash is not None and str(recorded_source_hash).casefold() != actual_source_hash:
                            problems.append("Special source-page SHA-256 differs from the preserved source page")
                        if isinstance(output_sha256, str) and HASH_RE.fullmatch(output_sha256) and output_sha256.casefold() != actual_source_hash:
                            problems.append("Special output page bytes differ from its mapped preserved source page")
            if not isinstance(output_sha256, str) or not HASH_RE.fullmatch(output_sha256):
                problems.append("Special output page SHA-256 is invalid")
            else:
                mapped_hashes.append(output_sha256.casefold())
        if output_numbers != list(range(1, actual_page_count + 1)):
            problems.append("Special output page mapping is not continuous")
        if mapped_hashes != list(actual_page_hashes):
            problems.append("Special output page mapping hashes differ from actual output pages")
        for source_path, included_pages in component_included.items():
            if sorted(included_pages) != sorted(mapped_source_pages.get(source_path, [])):
                problems.append("Special component included-page list differs from the actual output mapping")
    return problems


def _record_path(record: Mapping[str, Any]) -> Optional[str]:
    value = _first(
        record,
        ("relative_path", "output_path", "final_path", "target_path", "archive_path", "archive", "output", "path"),
    )
    return str(value).replace("\\", "/") if isinstance(value, (str, Path)) else None


def _match_fallback(
    documents: Sequence[Mapping[str, Any]], relative: str, series: str, volume: str
) -> Optional[Mapping[str, Any]]:
    best: Optional[Mapping[str, Any]] = None
    best_score = -1
    for record in documents:
        score = 0
        raw_path = _record_path(record)
        if raw_path:
            normalized = raw_path.lstrip("./")
            if normalized == relative:
                score += 100
            elif PurePosixPath(normalized).name == PurePosixPath(relative).name:
                score += 50
        doc_series = _first(record, ("series", "Series", "series_name"))
        if isinstance(doc_series, str) and _series_key(doc_series) == _series_key(series):
            score += 10
        doc_volume = _decimal_identity(str(_first(record, ("volume", "Volume", "volume_number")) or ""))
        if doc_volume == volume:
            score += 10
        if score > best_score and score >= 20:
            best = record
            best_score = score
    return best


def _range_from_record(record: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    pairs = (
        ("start_page", "end_page"), ("page_start", "page_end"),
        ("source_start", "source_end"), ("start", "end"),
    )
    for start_key, end_key in pairs:
        if start_key in record and end_key in record:
            try:
                start = int(record[start_key])
                end = int(record[end_key])
            except (TypeError, ValueError):
                return None
            return start, end
    span = record.get("span") or record.get("page_span")
    if isinstance(span, str):
        match = re.fullmatch(r"\s*(\d+)\s*(?:\.\.|-|–)\s*(\d+)\s*", span)
        if match:
            return int(match.group(1)), int(match.group(2))
    if isinstance(span, (list, tuple)) and len(span) == 2:
        try:
            return int(span[0]), int(span[1])
        except (TypeError, ValueError):
            return None
    return None


def _coverage_summary(record: Mapping[str, Any]) -> Tuple[Optional[int], List[Tuple[int, int]]]:
    expected_raw = _first(record, ("source_page_count", "total_source_pages", "total_pages", "page_count"))
    expected: Optional[int]
    try:
        expected = int(expected_raw) if expected_raw is not None else None
    except (TypeError, ValueError):
        expected = None
    ranges: List[Tuple[int, int]] = []
    own = _range_from_record(record)
    if own is not None:
        ranges.append(own)
    for key in ("ranges", "spans", "page_ranges", "covered_ranges", "coverage"):
        children = record.get(key)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, Mapping):
                    span = _range_from_record(child)
                    if span is not None:
                        ranges.append(span)
                elif isinstance(child, (list, tuple)) and len(child) == 2:
                    try:
                        ranges.append((int(child[0]), int(child[1])))
                    except (TypeError, ValueError):
                        pass
    return expected, ranges


def _fallback_document_errors(
    document: Mapping[str, Any],
    actual_page_count: Optional[int] = None,
    plan_base: Optional[Path] = None,
    overlap_targets: Sequence[Mapping[str, Any]] = (),
    current_page_hashes: Sequence[str] = (),
    current_page_perceptual: Sequence[str] = (),
    current_page_dimensions: Sequence[Sequence[int]] = (),
) -> List[str]:
    problems: List[str] = []
    chapter = _first(document, ("chapter", "Number", "chapter_number"))
    if chapter not in (None, "", False):
        problems.append("chapter must be null for a volume fallback")
    volume = _first(document, ("volume", "Volume", "volume_number"))
    if _decimal_identity(str(volume or "")) is None:
        problems.append("confirmed numeric volume is missing")
    reason = _first(document, ("fallback_reason", "reason", "boundary_failure_reason"))
    if not isinstance(reason, str) or not reason.strip():
        problems.append("fallback reason is missing")
    source_hash = document.get("source_sha256")
    if not isinstance(source_hash, str) or not HASH_RE.fullmatch(source_hash):
        problems.append("source SHA-256 is missing")
    source_value = document.get("source")
    if not isinstance(source_value, str) or not source_value.strip():
        problems.append("source path is missing")
    elif isinstance(source_hash, str) and HASH_RE.fullmatch(source_hash):
        source_path = Path(source_value)
        if not source_path.is_absolute():
            safe_source = _safe_relative(source_value)
            if safe_source is None:
                problems.append("source path is unsafe")
                source_path = Path()
            else:
                source_path = (plan_base or Path.cwd()) / safe_source
        if source_path != Path():
            source_path = _absolute(source_path)
            if source_path.is_symlink() or not source_path.is_file():
                problems.append("hashed source file is missing or unsafe")
            elif _sha256(source_path) != source_hash.casefold():
                problems.append("source SHA-256 does not match the source file")
    boundary_method = document.get("boundary_method")
    if not isinstance(boundary_method, str) or not boundary_method.strip():
        problems.append("boundary method is missing")
    if _first(document, ("series_confirmed", "confirmed_series")) is not True:
        problems.append("high-confidence series identity is not explicitly confirmed")
    if _first(document, ("volume_confirmed", "confirmed_volume", "identity_confirmed")) is not True:
        problems.append("volume identity is not explicitly confirmed")
    if _first(document, ("single_complete_volume", "exactly_one_complete_volume")) is not True:
        problems.append("exactly one complete source volume is not explicitly confirmed")
    if _first(document, ("all_pages_readable", "pages_readable")) is not True:
        problems.append("all source pages are not explicitly confirmed readable")
    if _first(document, ("natural_order_confirmed", "natural_page_order")) is not True:
        problems.append("natural page order is not explicitly confirmed")
    attempted = document.get("attempted_evidence")
    if not isinstance(attempted, list) or not attempted or not all(isinstance(item, str) and item.strip() for item in attempted):
        problems.append("attempted evidence is missing")
    if not isinstance(document.get("ocr_used"), bool):
        problems.append("OCR usage is not explicitly recorded")
    if document.get("ocr_used") is True and document.get("ocr_authorized") is not True:
        problems.append("OCR was used without recorded authorization")
    if document.get("cross_package_overlap_checked") is not True:
        problems.append("cross-package overlap audit is not explicitly confirmed")
    if document.get("content_overlap_detected") is True:
        problems.append("cross-package content overlap remains unresolved")
    fallback_id = document.get("fallback_id")
    if not isinstance(fallback_id, str) or not fallback_id.strip():
        problems.append("stable fallback ID is missing")
    overlap = document.get("overlap_audit")
    if not isinstance(overlap, Mapping):
        problems.append("machine-readable overlap audit is missing")
    else:
        compared = overlap.get("compared_identities")
        expected_compared = [
            {
                "path": target.get("path"),
                "archive_sha256": target.get("sha256"),
                "canonical_identity": target.get("canonical_identity"),
            }
            for target in overlap_targets
        ]
        if not isinstance(compared, list):
            problems.append("overlap audit compared identities are missing")
        elif compared != expected_compared:
            problems.append("overlap audit does not cover every active package identity and archive hash")
        if overlap.get("page_sha256_checked") is not True:
            problems.append("overlap audit lacks page SHA-256 comparison")
        if overlap.get("perceptual_candidates_checked") is not True:
            problems.append("overlap audit lacks perceptual-candidate comparison")
        if overlap.get("visual_review_complete") is not True:
            problems.append("overlap audit visual decisions are incomplete")
        if overlap.get("result") not in {"no-overlap", "resolved"}:
            problems.append("overlap audit result is unresolved")
        if not isinstance(overlap.get("audit_record_id"), str) or not overlap.get("audit_record_id", "").strip():
            problems.append("overlap audit record ID is missing")
        comparisons = overlap.get("page_sha256_comparisons")
        expected_comparisons = []
        current_set = set(current_page_hashes)
        for target in overlap_targets:
            expected_comparisons.append({
                "path": target.get("path"),
                "shared_sha256": sorted(current_set & set(target.get("page_sha256", []))),
            })
        if comparisons != expected_comparisons:
            problems.append("overlap audit page SHA-256 comparison differs from actual packages")
        threshold = overlap.get("perceptual_distance", DEFAULT_PERCEPTUAL_DISTANCE)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 16:
            problems.append("overlap audit perceptual distance must be an integer from 0 through 16")
            threshold = DEFAULT_PERCEPTUAL_DISTANCE
        if overlap.get("perceptual_hash_algorithm") != "dhash-88-color":
            problems.append("overlap audit perceptual hash algorithm is missing or unsupported")
        expected_candidates: List[Dict[str, Any]] = []
        if len(current_page_hashes) != len(current_page_perceptual) or len(current_page_hashes) != len(current_page_dimensions):
            problems.append("current fallback perceptual evidence is incomplete")
        else:
            for target in overlap_targets:
                target_hashes = list(target.get("page_sha256", []))
                target_perceptual = list(target.get("page_perceptual_hash", []))
                target_dimensions = list(target.get("page_dimensions", []))
                if not (len(target_hashes) == len(target_perceptual) == len(target_dimensions)):
                    problems.append("compared package perceptual evidence is incomplete")
                    continue
                for current_index, (current_hash, current_phash, current_dimensions) in enumerate(
                    zip(current_page_hashes, current_page_perceptual, current_page_dimensions), start=1
                ):
                    for target_index, (target_hash, target_phash, target_size) in enumerate(
                        zip(target_hashes, target_perceptual, target_dimensions), start=1
                    ):
                        try:
                            current_size = (int(current_dimensions[0]), int(current_dimensions[1]))
                            compared_size = (int(target_size[0]), int(target_size[1]))
                            distance = _hamming_hex(str(current_phash), str(target_phash))
                        except (IndexError, TypeError, ValueError):
                            problems.append("compared package perceptual evidence is invalid")
                            continue
                        if distance <= threshold and _similar_aspect(current_size, compared_size):
                            expected_candidates.append({
                                "fallback_page": current_index,
                                "fallback_page_sha256": current_hash,
                                "compared_path": target.get("path"),
                                "compared_page": target_index,
                                "compared_page_sha256": target_hash,
                                "hamming_distance": distance,
                            })
        expected_candidates.sort(key=lambda item: (str(item["compared_path"]), int(item["fallback_page"]), int(item["compared_page"])))
        recorded_candidates = overlap.get("perceptual_candidates")
        if not isinstance(recorded_candidates, list):
            problems.append("overlap audit perceptual candidates must be recorded as an array")
        elif recorded_candidates != expected_candidates:
            problems.append("overlap audit perceptual candidates differ from actual package pages")
        decisions = overlap.get("visual_decisions")
        if not isinstance(decisions, list):
            problems.append("overlap audit visual decisions must be recorded as an array")
        else:
            expected_keys = {
                (
                    item["fallback_page"], item["fallback_page_sha256"], item["compared_path"],
                    item["compared_page"], item["compared_page_sha256"], item["hamming_distance"],
                )
                for item in expected_candidates
            }
            decision_keys: Set[Tuple[Any, ...]] = set()
            for decision in decisions:
                if not isinstance(decision, Mapping):
                    problems.append("overlap audit visual decision is invalid")
                    continue
                key = (
                    decision.get("fallback_page"), decision.get("fallback_page_sha256"),
                    decision.get("compared_path"), decision.get("compared_page"),
                    decision.get("compared_page_sha256"), decision.get("hamming_distance"),
                )
                decision_keys.add(key)
                if not isinstance(decision.get("duplicate"), bool):
                    problems.append("overlap audit visual decision lacks a boolean duplicate result")
                if not isinstance(decision.get("reviewer"), str) or not decision.get("reviewer", "").strip():
                    problems.append("overlap audit visual decision lacks a reviewer")
                if not isinstance(decision.get("reason"), str) or not decision.get("reason", "").strip():
                    problems.append("overlap audit visual decision lacks a reason")
            if decision_keys != expected_keys or len(decisions) != len(expected_candidates):
                problems.append("overlap audit visual decisions do not cover every actual perceptual candidate exactly once")
        if any(item["shared_sha256"] for item in expected_comparisons) and overlap.get("result") != "resolved":
            problems.append("exact shared pages require an explicitly resolved overlap result")
    expected, ranges = _coverage_summary(document)
    explicit_complete = _first(
        document,
        ("coverage_complete", "exact_coverage", "covered_exactly_once", "coverage_result"),
    )
    if isinstance(explicit_complete, str):
        normalized_complete = explicit_complete.casefold().replace("_", "-").strip()
        explicit_complete = normalized_complete in {
            "complete", "exact", "exactly-once", "full", "passed", "pass", "true"
        }
    if explicit_complete is False:
        problems.append("page coverage is explicitly incomplete")
    if actual_page_count is not None and expected != actual_page_count:
        problems.append("documented source page count does not equal actual fallback page count")
    if expected is not None and ranges:
        counts = [0] * max(expected, 0)
        for start, end in ranges:
            if start < 1 or end < start or end > expected:
                problems.append("page span is outside the source range")
                continue
            for page in range(start, end + 1):
                counts[page - 1] += 1
        if counts and any(count != 1 for count in counts):
            problems.append("source pages are not covered exactly once")
    else:
        problems.append("exact full-page ranges are not documented")
    return sorted(set(problems))


def _gap_numbers(value: Any) -> Set[int]:
    numbers: Set[int] = set()
    if isinstance(value, int):
        numbers.add(value)
    elif isinstance(value, str):
        for part in re.split(r"\s*,\s*", value):
            match = re.fullmatch(r"\s*(\d+)\s*(?:-|\.\.|–)\s*(\d+)\s*", part)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                if end >= start:
                    numbers.update(range(start, end + 1))
            elif part.strip().isdigit():
                numbers.add(int(part.strip()))
    elif isinstance(value, Mapping):
        start = _first(value, ("start", "from", "first", "start_chapter"))
        end = _first(value, ("end", "to", "last", "end_chapter"))
        if start is not None and end is not None:
            try:
                begin, finish = int(start), int(end)
                if finish >= begin:
                    numbers.update(range(begin, finish + 1))
            except (TypeError, ValueError):
                pass
        for key in ("chapter", "chapters", "numbers", "range", "missing"):
            if key in value:
                numbers.update(_gap_numbers(value[key]))
    elif isinstance(value, list):
        for item in value:
            numbers.update(_gap_numbers(item))
    return numbers


def _intentional_gaps(plan: Optional[Any]) -> Tuple[Dict[str, Set[int]], List[Dict[str, Any]]]:
    by_series: Dict[str, Set[int]] = defaultdict(set)
    invalid: List[Dict[str, Any]] = []
    if plan is None:
        return by_series, invalid
    keys = ("deliberate_missing_ranges", "intentional_gaps", "confirmed_source_gaps")
    for record in _walk_dicts(plan):
        for key in keys:
            if key not in record:
                continue
            entries = record[key]
            series = _first(record, ("series", "Series", "series_name"))
            entry_list = entries if isinstance(entries, list) else [entries]
            for entry in entry_list:
                entry_series = series
                if isinstance(entry, Mapping):
                    entry_series = _first(entry, ("series", "Series", "series_name")) or series
                    reason = _first(entry, ("reason", "source_reason", "missing_source"))
                    confirmed = _first(entry, ("user_confirmation", "confirmed", "source_confirmed"))
                    if not reason or confirmed is False:
                        invalid.append(_issue("invalid_intentional_gap", "intentional gap lacks a reason or is unconfirmed", details=dict(entry)))
                numbers = _gap_numbers(entry)
                if not numbers or not isinstance(entry_series, str):
                    invalid.append(_issue("invalid_intentional_gap", "intentional gap lacks a series or numeric range"))
                    continue
                by_series[_series_key(entry_series)].update(numbers)
    return by_series, invalid


def _decode_image(data: bytes) -> Optional[str]:
    if PILImage is None:
        return "__PILLOW_UNAVAILABLE__"
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # Pillow raises several plugin-specific exception types.
        return str(exc)
    return None


def _image_evidence(data: bytes) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, int]]]:
    if PILImage is None:
        return "__PILLOW_UNAVAILABLE__", None, None
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            image.load()
            dimensions = (int(image.width), int(image.height))
            if dimensions[0] < 1 or dimensions[1] < 1:
                raise ValueError("image has zero dimensions")
            resampling = getattr(getattr(PILImage, "Resampling", PILImage), "LANCZOS")
            rgb = image.convert("RGB").resize((8, 8), resampling)
            rgb_pixels = list(rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata())
            grayscale = image.convert("L").resize((9, 8), resampling)
            pixels = list(
                grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata()
            )
    except Exception as exc:
        return str(exc), None, None
    difference_bits = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            difference_bits = (difference_bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    pixel_count = len(rgb_pixels)
    averages = tuple(sum(pixel[channel] for pixel in rgb_pixels) // pixel_count for channel in range(3))
    color_bits = (averages[0] << 16) | (averages[1] << 8) | averages[2]
    return None, f"{((difference_bits << 24) | color_bits):022x}", dimensions


def _hamming_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _similar_aspect(left: Tuple[int, int], right: Tuple[int, int]) -> bool:
    left_cross = left[0] * right[1]
    right_cross = right[0] * left[1]
    scale = max(left_cross, right_cross)
    return scale > 0 and abs(left_cross - right_cross) / scale <= 0.01


def _validate_archive(path: Path, library: Path) -> Dict[str, Any]:
    relative = path.relative_to(library).as_posix()
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "path": relative,
        "sha256": None,
        "page_count": 0,
        "page_sha256": [],
        "page_perceptual_hash": [],
        "page_dimensions": [],
        "series": None,
        "number": None,
        "volume": None,
        "format": None,
        "title": None,
        "errors": errors,
        "warnings": warnings,
    }
    if path.is_symlink():
        errors.append(_issue("archive_symlink", "archive path itself must not be a symlink", relative))
        return result
    try:
        result["sha256"] = _sha256(path)
    except OSError as exc:
        errors.append(_issue("archive_read_failed", str(exc), relative))
        return result
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                errors.append(_issue("archive_member_limit", "archive exceeds the member-count safety limit", relative, actual=len(infos), limit=MAX_ARCHIVE_MEMBERS))
                return result
            total_uncompressed = sum(max(0, info.file_size) for info in infos)
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                errors.append(_issue("archive_uncompressed_limit", "archive exceeds the total uncompressed-byte safety limit", relative, actual=total_uncompressed, limit=MAX_ARCHIVE_UNCOMPRESSED_BYTES))
                return result
            safe_infos: List[zipfile.ZipInfo] = []
            root_comic: List[zipfile.ZipInfo] = []
            any_comic: List[zipfile.ZipInfo] = []
            image_infos: List[zipfile.ZipInfo] = []
            seen_names: Set[str] = set()
            unsafe_expansion = False
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                safe = _safe_relative(normalized.rstrip("/")) if normalized.rstrip("/") else None
                if safe is None:
                    errors.append(_issue("unsafe_archive_path", "archive member is absolute or traverses directories", relative, member=info.filename))
                    continue
                if _zip_member_is_symlink(info):
                    errors.append(_issue("archive_symlink", "archive contains a symlink member", relative, member=info.filename))
                    continue
                if info.flag_bits & 0x1:
                    errors.append(_issue("encrypted_archive_member", "archive member is encrypted", relative, member=info.filename))
                    continue
                if info.file_size > 0:
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > MAX_MEMBER_EXPANSION_RATIO:
                        errors.append(_issue("archive_expansion_ratio", "archive member exceeds the expansion-ratio safety limit", relative, member=info.filename, ratio=ratio, limit=MAX_MEMBER_EXPANSION_RATIO))
                        unsafe_expansion = True
                        continue
                key = normalized.casefold()
                if key in seen_names:
                    errors.append(_issue("duplicate_archive_member", "archive contains duplicate member names", relative, member=info.filename))
                seen_names.add(key)
                safe_infos.append(info)
                if not info.is_dir() and PurePosixPath(normalized).name.casefold() == "comicinfo.xml":
                    any_comic.append(info)
                    if len(PurePosixPath(normalized).parts) == 1 and normalized == "ComicInfo.xml":
                        root_comic.append(info)
                if not info.is_dir() and PurePosixPath(normalized).suffix.casefold() in IMAGE_SUFFIXES:
                    image_infos.append(info)
            if not any(info.flag_bits & 0x1 for info in infos) and not unsafe_expansion:
                try:
                    bad_crc = archive.testzip()
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    errors.append(_issue("zip_crc_failed", str(exc), relative))
                else:
                    if bad_crc is not None:
                        errors.append(_issue("zip_crc_failed", "CRC failed for member {}".format(bad_crc), relative))
            if len(root_comic) != 1 or len(any_comic) != 1:
                errors.append(_issue(
                    "comicinfo_root_uniqueness",
                    "archive must contain exactly one case-exact root ComicInfo.xml",
                    relative,
                    root_count=len(root_comic), total_count=len(any_comic),
                ))
            root: Optional[ET.Element] = None
            if len(root_comic) == 1:
                info = root_comic[0]
                if info.file_size > 8 * 1024 * 1024:
                    errors.append(_issue("comicinfo_too_large", "ComicInfo.xml exceeds the safety limit", relative))
                else:
                    try:
                        xml_data = archive.read(info)
                        lowered = xml_data.lower()
                        if b"<!doctype" in lowered or b"<!entity" in lowered:
                            raise ValueError("DTD and entity declarations are forbidden")
                        root = ET.fromstring(xml_data)
                    except Exception as exc:
                        errors.append(_issue("comicinfo_parse_failed", str(exc), relative))
            result["page_count"] = len(image_infos)
            names = [item.filename.replace("\\", "/") for item in image_infos]
            nested_pages = [name for name in names if len(PurePosixPath(name).parts) != 1]
            if nested_pages:
                errors.append(_issue(
                    "pages_not_at_archive_root", "image pages must be stored at the archive root", relative,
                    members=nested_pages,
                ))
            if names != sorted(names, key=_natural_key):
                errors.append(_issue("page_order_not_natural", "image members are not stored in natural page order", relative))
            pillow_unavailable = False
            for info in image_infos:
                if PILImage is None:
                    pillow_unavailable = True
                    break
                try:
                    image_data = archive.read(info)
                    result["page_sha256"].append(hashlib.sha256(image_data).hexdigest())
                    decode_error, perceptual_hash, dimensions = _image_evidence(image_data)
                    if perceptual_hash is not None and dimensions is not None:
                        result["page_perceptual_hash"].append(perceptual_hash)
                        result["page_dimensions"].append(list(dimensions))
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    decode_error = str(exc)
                if decode_error == "__PILLOW_UNAVAILABLE__":
                    pillow_unavailable = True
                    break
                if decode_error:
                    errors.append(_issue("image_decode_failed", decode_error, relative, member=info.filename))
            if pillow_unavailable:
                errors.append(_issue("pillow_required", "Pillow is required; full image decoding cannot be skipped", relative))
            if not image_infos:
                errors.append(_issue("archive_has_no_pages", "archive has no supported image pages", relative))
            if root is not None:
                page_count_text = _xml_text(root, "PageCount")
                try:
                    declared = int(page_count_text) if page_count_text is not None else None
                except ValueError:
                    declared = None
                if declared != len(image_infos):
                    errors.append(_issue(
                        "pagecount_mismatch", "ComicInfo PageCount does not equal actual image count", relative,
                        declared=page_count_text, actual=len(image_infos),
                    ))
                result["series"] = _xml_text(root, "Series")
                result["localized_series"] = _xml_text(root, "LocalizedSeries")
                result["series_sort"] = _xml_text(root, "SeriesSort")
                result["number"] = _xml_text(root, "Number")
                result["volume"] = _xml_text(root, "Volume")
                result["format"] = _xml_text(root, "Format")
                result["title"] = _xml_text(root, "Title")
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        errors.append(_issue("invalid_zip", str(exc), relative))
    return result


def _verified_source_page_evidence(path: Path) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Return page evidence only when a preserved source archive fully validates."""
    try:
        result = _validate_archive(path, path.parent)
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if result.get("errors"):
        return None, "source archive validation failed: {}".format(result["errors"])
    hashes = result.get("page_sha256", [])
    perceptual = result.get("page_perceptual_hash", [])
    dimensions = result.get("page_dimensions", [])
    if not (len(hashes) == len(perceptual) == len(dimensions) == int(result.get("page_count") or 0)):
        return None, "source archive page evidence is incomplete"
    return [
        {"sha256": digest, "perceptual_hash": perceptual[index], "dimensions": dimensions[index]}
        for index, digest in enumerate(hashes)
    ], None


def _hash_records(value: Any, default_root: Optional[Path] = None) -> List[Tuple[str, str, Optional[Path]]]:
    """Extract ``(path, sha256, root)`` records from common manifest shapes."""
    records: List[Tuple[str, str, Optional[Path]]] = []
    if isinstance(value, str) and HASH_RE.fullmatch(value):
        return records
    if isinstance(value, Mapping):
        own_root = default_root
        root_value = _first(value, ("root", "library", "base_path", "source_root", "formal_library"))
        if isinstance(root_value, str):
            own_root = Path(root_value)
        direct_path = _first(value, ("path", "relative_path", "source", "archive"))
        direct_hash = _first(value, ("sha256", "hash", "checksum"))
        has_direct = isinstance(direct_path, str) and isinstance(direct_hash, str) and HASH_RE.fullmatch(direct_hash)
        if has_direct:
            records.append((direct_path, direct_hash.casefold(), own_root))
        for key, child in value.items():
            if (
                not has_direct and isinstance(child, str) and HASH_RE.fullmatch(child)
                and isinstance(key, str)
                and key.casefold() not in {"sha256", "hash", "checksum", "manifest_sha256", "tree_sha256"}
            ):
                records.append((key, child.casefold(), own_root))
            elif isinstance(child, (Mapping, list)):
                records.extend(_hash_records(child, own_root))
    elif isinstance(value, list):
        for child in value:
            records.extend(_hash_records(child, default_root))
    deduplicated: Dict[Tuple[str, str], Tuple[str, str, Optional[Path]]] = {}
    for path, expected, root in records:
        deduplicated[(str(root) if root else "", path)] = (path, expected, root)
    return list(deduplicated.values())


def _named_values(container: Any, names: Sequence[str]) -> List[Any]:
    values: List[Any] = []
    if not isinstance(container, (Mapping, list)):
        return values
    for record in _walk_dicts(container):
        for name in names:
            if name in record:
                values.append(record[name])
    return values


def _resolve_record_path(raw: str, root: Optional[Path], fallback_root: Path) -> Optional[Path]:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    base = root if root is not None else fallback_root
    safe = _safe_relative(raw)
    if safe is None:
        return None
    return base / safe


def _unaffected_expectations(
    plan: Optional[Any], baseline: Optional[Any], library: Path, baseline_base: Path
) -> Dict[str, str]:
    if baseline is None:
        return {}
    all_records: List[Tuple[str, str, Optional[Path]]] = []
    named = _named_values(baseline, ("unaffected_archives", "archive_hashes", "archives", "files"))
    if named:
        for value in named:
            all_records.extend(_hash_records(value, library))
    else:
        all_records.extend(_hash_records(baseline, library))
    expected_by_path: Dict[str, str] = {}
    expected_by_name: Dict[str, str] = {}
    for raw, digest, root in all_records:
        resolved = _resolve_record_path(raw, root, baseline_base)
        if resolved is not None:
            try:
                relative = resolved.resolve(strict=False).relative_to(library.resolve()).as_posix()
                expected_by_path[relative] = digest
            except ValueError:
                expected_by_path[raw.replace("\\", "/").lstrip("./")] = digest
        expected_by_name[PurePosixPath(raw.replace("\\", "/")).name] = digest
    requested: Set[str] = set()
    for value in _named_values(plan, ("unaffected_archives",)) if plan is not None else []:
        if isinstance(value, Mapping):
            requested.update(str(item) for item in value.keys())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    requested.add(item.replace("\\", "/").lstrip("./"))
                elif isinstance(item, Mapping):
                    item_path = _record_path(item)
                    if item_path:
                        requested.add(item_path.lstrip("./"))
        elif isinstance(value, str):
            requested.add(value.replace("\\", "/").lstrip("./"))
    if not requested:
        return expected_by_path
    selected: Dict[str, str] = {}
    for raw in sorted(requested):
        if raw in expected_by_path:
            selected[raw] = expected_by_path[raw]
        elif PurePosixPath(raw).name in expected_by_name:
            selected[raw] = expected_by_name[PurePosixPath(raw).name]
    return selected


def _validate_named_hashes(plan: Optional[Any], plan_base: Path, names: Sequence[str], kind: str) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    if plan is None:
        return errors
    values = _named_values(plan, names)
    records: List[Tuple[str, str, Optional[Path]]] = []
    for value in values:
        records.extend(_hash_records(value, None))
    for raw, expected, root in sorted(records, key=lambda row: row[0]):
        path = _resolve_record_path(raw, root, plan_base)
        if path is None:
            errors.append(_issue("unsafe_{}_hash_path".format(kind), "{} hash path is unsafe".format(kind), raw))
            continue
        if not path.is_file():
            errors.append(_issue("{}_hash_target_missing".format(kind), "hashed {} file is missing".format(kind), path))
            continue
        try:
            actual = _sha256(path)
        except OSError as exc:
            errors.append(_issue("{}_hash_read_failed".format(kind), str(exc), path))
            continue
        if actual != expected:
            errors.append(_issue("{}_hash_changed".format(kind), "{} SHA-256 differs from the recorded value".format(kind), path, expected=expected, actual=actual))
    return errors


def _validate_source_hashes(plan: Optional[Any], plan_base: Path) -> List[Dict[str, Any]]:
    return _validate_named_hashes(plan, plan_base, ("source_hashes", "source_manifest", "source_files"), "source")


def _validate_page_coverage(plan: Optional[Any]) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    if plan is None:
        return errors
    grouped: Dict[str, Dict[str, Any]] = {}
    for record in _walk_dicts(plan):
        source = _first(record, ("source_file", "source_path", "source_pdf", "source"))
        expected, spans = _coverage_summary(record)
        explicit = _first(
            record,
            ("coverage_complete", "exact_coverage", "covered_exactly_once", "coverage_result"),
        )
        if isinstance(explicit, str):
            normalized_explicit = explicit.casefold().replace("_", "-").strip()
            explicit = normalized_explicit in {
                "complete", "exact", "exactly-once", "full", "passed", "pass", "true"
            }
        if isinstance(source, str) and (spans or expected is not None or explicit is not None):
            item = grouped.setdefault(source, {"ranges": [], "expected": None, "explicit": []})
            item["ranges"].extend(spans)
            if expected is not None:
                if item["expected"] is not None and item["expected"] != expected:
                    errors.append(_issue("source_page_count_conflict", "conflicting source page counts in plan", source))
                item["expected"] = expected
            if explicit is not None:
                item["explicit"].append(bool(explicit))
    for source, item in sorted(grouped.items()):
        expected = item["expected"]
        ranges = item["ranges"]
        if any(flag is False for flag in item["explicit"]):
            errors.append(_issue("source_coverage_incomplete", "plan explicitly marks source coverage incomplete", source))
        if expected is None or not ranges:
            if not item["explicit"] or not all(item["explicit"]):
                errors.append(_issue("source_coverage_undocumented", "source coverage lacks page count, ranges, or an explicit exact-coverage result", source))
            continue
        if expected < 1:
            errors.append(_issue("invalid_source_page_count", "source page count must be positive", source))
            continue
        counts = [0] * expected
        invalid = False
        for start, end in ranges:
            if start < 1 or end < start or end > expected:
                invalid = True
                continue
            for page in range(start, end + 1):
                counts[page - 1] += 1
        if invalid or any(count != 1 for count in counts):
            missing = [index + 1 for index, count in enumerate(counts) if count == 0]
            overlap = [index + 1 for index, count in enumerate(counts) if count > 1]
            errors.append(_issue(
                "source_page_coverage_failed", "source pages are not covered exactly once", source,
                missing=missing, overlap=overlap,
            ))
    return errors


def validate_library(
    library: Path,
    plan: Optional[Any] = None,
    baseline: Optional[Any] = None,
    plan_base: Optional[Path] = None,
    baseline_base: Optional[Path] = None,
    require_checksums: bool = False,
) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    requested = _absolute(library)
    result: Dict[str, Any] = {
        "command": "validate",
        "library": str(requested),
        "ok": False,
        "errors": errors,
        "warnings": warnings,
        "archives": [],
        "summary": {"archive_count": 0, "page_count": 0, "series_count": 0},
    }
    plan_base = plan_base or Path.cwd()
    baseline_base = baseline_base or Path.cwd()
    if library.is_symlink():
        errors.append(_issue("library_symlink_refused", "library path must not be a symlink", requested))
        return result
    if not requested.exists() or not requested.is_dir():
        errors.append(_issue("library_missing", "library path is not an existing directory", requested))
        return result
    library = requested.resolve()
    errors.extend(_tree_safety_errors(library))
    archives = _archive_paths(library)
    if not archives:
        errors.append(_issue("library_has_no_archives", "library contains no CBZ/ZIP archives", library))
    for path in archives:
        if path.parent == library:
            errors.append(_issue("media_at_library_root", "archive must be nested under a series directory", path))
        archive_result = _validate_archive(path, library)
        result["archives"].append(archive_result)
        errors.extend(archive_result["errors"])
        warnings.extend(archive_result["warnings"])

    default_policy, policies = _plan_policies(plan)
    errors.extend(_policy_errors(plan))
    fallback_docs = _fallback_documents(plan)
    special_docs = _special_documents(plan)
    intentional, invalid_gaps = _intentional_gaps(plan)
    errors.extend(invalid_gaps)
    folder_series: Dict[str, Set[str]] = defaultdict(set)
    series_folders: Dict[str, Set[str]] = defaultdict(set)
    identities: Dict[Tuple[str, str, str], List[str]] = defaultdict(list)
    chapter_numbers: Dict[str, Set[Decimal]] = defaultdict(set)
    chapter_volume_presence: Dict[str, Set[bool]] = defaultdict(set)
    display_series: Dict[str, str] = {}
    unintended_gaps: Dict[str, List[int]] = {}

    for archive in result["archives"]:
        if archive["errors"] and archive.get("series") is None:
            continue
        relative = archive["path"]
        parts = PurePosixPath(relative).parts
        series = archive.get("series")
        if not isinstance(series, str) or not series.strip():
            errors.append(_issue("series_missing", "ComicInfo Series is missing", relative))
            continue
        key = _series_key(series)
        display_series.setdefault(key, series.strip())
        folder = parts[0] if parts else ""
        folder_series[folder].add(key)
        series_folders[key].add(folder)
        localized = archive.get("localized_series")
        series_sort = archive.get("series_sort")
        for field, value in (("LocalizedSeries", localized), ("SeriesSort", series_sort)):
            if value and _series_key(str(value)) != key:
                errors.append(_issue("series_identity_mix", "{} differs from Series".format(field), relative, Series=series, field_value=value))
        number_raw = archive.get("number")
        volume_raw = archive.get("volume")
        number = _decimal_identity(number_raw)
        volume = _decimal_identity(volume_raw)
        fmt = str(archive.get("format") or "").strip()
        special_match = SPECIAL_TOKEN_RE.match(str(number_raw or ""))
        is_special = fmt.casefold() == "special" or special_match is not None
        policy = policies.get(key, default_policy).casefold().replace("_", "-")
        if is_special:
            if fmt.casefold() != "special" or special_match is None:
                errors.append(_issue("invalid_special_identity", "Special requires Format=Special and an SP Number", relative))
            identity = special_match.group(1) if special_match else str(number_raw or "")
            identities[(key, "special", identity)].append(relative)
            filename_match = SPECIAL_FILENAME_RE.search(PurePosixPath(relative).stem)
            if filename_match is None or _decimal_identity(filename_match.group(1)) != _decimal_identity(identity):
                errors.append(_issue("special_filename_identity_mismatch", "Special filename SP token differs from ComicInfo Number", relative, comicinfo_number=number_raw))
            if volume is not None:
                errors.append(_issue("invalid_special_identity", "Special must omit ComicInfo Volume", relative, volume=volume_raw))
            title = archive.get("title")
            if not isinstance(title, str) or re.search(r"[\u3400-\u9fff]", title) is None:
                errors.append(_issue("META008", "Special requires a reliable Chinese Title", relative))
            document = _match_special(special_docs, relative, series, str(number_raw or ""))
            if document is None:
                errors.append(_issue("SPECIAL_RANGE_INCOMPLETE", "Special lacks a matching complete-range plan/audit record", relative))
            else:
                for problem in _special_document_errors(
                    document,
                    int(archive.get("page_count") or 0),
                    archive.get("page_sha256", []),
                    plan_base,
                ):
                    errors.append(_issue("SPECIAL_RANGE_INCOMPLETE", problem, relative))
            continue
        if number is not None:
            chapter_identity = "{}:{}".format(volume, number) if policy == "volume-aware-chapter" and volume is not None else number
            identities[(key, "chapter", chapter_identity)].append(relative)
            if policy == "continuous-chapter":
                try:
                    chapter_numbers[key].add(Decimal(number))
                except InvalidOperation:
                    pass
            chapter_volume_presence[key].add(volume is not None)
            chapter_match = CHAPTER_TOKEN_RE.search(PurePosixPath(relative).stem)
            if chapter_match is None:
                errors.append(_issue("chapter_filename_identity_missing", "normal chapter filename lacks a chapter token", relative))
            elif _decimal_identity(chapter_match.group(1)) != number:
                errors.append(_issue("chapter_filename_identity_mismatch", "filename chapter token differs from ComicInfo Number", relative, filename_chapter=chapter_match.group(1), comicinfo_number=number))
            if policy == "continuous-chapter":
                if volume is not None:
                    errors.append(_issue("KAVITA_VOLUME_JUMP_RISK", "continuous-chapter item must omit ComicInfo Volume", relative))
                if VOLUME_TOKEN_RE.search(PurePosixPath(relative).stem):
                    errors.append(_issue("KAVITA_VOLUME_JUMP_RISK", "continuous-chapter filename must omit volume tokens", relative))
            elif policy == "volume-aware-chapter" and volume is None:
                errors.append(_issue("volume_aware_chapter_missing_volume", "volume-aware chapter requires ComicInfo Volume", relative))
            elif policy == "volume-aware-chapter":
                volume_match = VOLUME_VALUE_RE.search(PurePosixPath(relative).stem)
                if volume_match is None or _decimal_identity(volume_match.group(1)) != volume:
                    errors.append(_issue("volume_filename_identity_mismatch", "filename volume token differs from ComicInfo Volume", relative, comicinfo_volume=volume))
            elif policy == "volume-only":
                errors.append(_issue("volume_only_has_chapter", "volume-only series cannot contain a normal chapter identity", relative))
        elif volume is not None:
            identities[(key, "volume", volume)].append(relative)
            if policy == "volume-only":
                pass
            else:
                document = _match_fallback(fallback_docs, relative, series, volume)
                if document is None:
                    errors.append(_issue("UNDOCUMENTED_VOLUME_FALLBACK", "volume item in chapter policy lacks a matching volume-fallback plan record", relative))
                else:
                    overlap_targets = sorted(
                        (
                            {
                                "path": other.get("path"),
                                "sha256": other.get("sha256"),
                                "canonical_identity": _archive_canonical_identity(other),
                                "page_sha256": other.get("page_sha256", []),
                                "page_perceptual_hash": other.get("page_perceptual_hash", []),
                                "page_dimensions": other.get("page_dimensions", []),
                            }
                            for other in result["archives"]
                            if other.get("path") != relative
                            and isinstance(other.get("series"), str)
                            and _series_key(str(other.get("series"))) == key
                        ),
                        key=lambda item: str(item["path"]),
                    )
                    for problem in _fallback_document_errors(
                        document,
                        int(archive.get("page_count") or 0),
                        plan_base=plan_base,
                        overlap_targets=overlap_targets,
                        current_page_hashes=archive.get("page_sha256", []),
                        current_page_perceptual=archive.get("page_perceptual_hash", []),
                        current_page_dimensions=archive.get("page_dimensions", []),
                    ):
                        errors.append(_issue("invalid_volume_fallback", problem, relative))
            if CHAPTER_TOKEN_RE.search(PurePosixPath(relative).stem):
                errors.append(_issue("fallback_filename_has_chapter", "volume fallback filename must not contain a chapter token", relative))
            volume_match = VOLUME_VALUE_RE.search(PurePosixPath(relative).stem)
            if volume_match is None or _decimal_identity(volume_match.group(1)) != volume:
                errors.append(_issue("volume_filename_identity_mismatch", "fallback filename volume token differs from ComicInfo Volume", relative, comicinfo_volume=volume))
        else:
            errors.append(_issue("archive_identity_missing", "normal archive has neither Number nor Volume", relative))

    for folder, keys in sorted(folder_series.items()):
        if len(keys) > 1:
            errors.append(_issue("mixed_series_folder", "one series folder contains multiple ComicInfo Series identities", folder, series=sorted(display_series.get(key, key) for key in keys)))
    for key, folders in sorted(series_folders.items()):
        if len(folders) > 1:
            errors.append(_issue("series_split_across_folders", "one ComicInfo Series identity is split across library folders", display_series.get(key, key), folders=sorted(folders)))
    for identity, paths in sorted(identities.items()):
        if len(paths) > 1:
            code = "DUPLICATE_CHAPTER_IDENTITY" if identity[1] == "chapter" else "duplicate_series_identity"
            errors.append(_issue(code, "duplicate series/package identity", display_series.get(identity[0], identity[0]), kind=identity[1], identity=identity[2], archives=sorted(paths)))
    for key, presence in sorted(chapter_volume_presence.items()):
        if len(presence) > 1:
            errors.append(_issue("SERIES_IDENTITY_MIX", "normal chapters mix Volume-present and Volume-absent identities", display_series.get(key, key)))
            errors.append(_issue("PARTIAL_VOLUME_TAGGING", "only part of the normal chapter sequence carries Volume", display_series.get(key, key)))
    for key, numbers in sorted(chapter_numbers.items()):
        integral = sorted(int(number) for number in numbers if number == number.to_integral_value())
        if len(integral) < 2:
            continue
        missing = set(range(integral[0], integral[-1] + 1)) - set(integral)
        undocumented = sorted(missing - intentional.get(key, set()))
        if undocumented:
            unintended_gaps[key] = undocumented
            errors.append(_issue("UNINTENDED_GAP", "chapter sequence has undocumented integer gaps", display_series.get(key, key), chapters=undocumented))

    errors.extend(_validate_source_hashes(plan, plan_base))
    errors.extend(_validate_named_hashes(plan, plan_base, ("review_copy_hashes", "review_hashes"), "review_copy"))
    errors.extend(_validate_page_coverage(plan))
    expected_unaffected = _unaffected_expectations(plan, baseline, library, baseline_base)
    actual_hashes = {item["path"]: item.get("sha256") for item in result["archives"]}
    for relative, expected in sorted(expected_unaffected.items()):
        actual = actual_hashes.get(relative)
        if actual is None:
            errors.append(_issue("unaffected_archive_missing", "unaffected baseline archive is missing", relative, expected=expected))
        elif actual != expected:
            errors.append(_issue("unaffected_archive_changed", "unaffected archive SHA-256 changed", relative, expected=expected, actual=actual))

    audit: List[Dict[str, Any]] = []
    for key in sorted(series_folders):
        chapter_items = [item for item in identities if item[0] == key and item[1] == "chapter"]
        special_items = [item for item in identities if item[0] == key and item[1] == "special"]
        fallback_items = [item for item in identities if item[0] == key and item[1] == "volume"]
        duplicate_items = [
            {"kind": item[1], "identity": item[2], "archives": sorted(identities[item])}
            for item in identities
            if item[0] == key and len(identities[item]) > 1
        ]
        presence = chapter_volume_presence.get(key, set())
        audit.append(
            {
                "series": display_series.get(key, key),
                "identity_policy": policies.get(key, default_policy),
                "normal_chapter_count": len(chapter_items),
                "special_count": len(special_items),
                "volume_fallback_count": len(fallback_items),
                "has_chapters_with_volume": True in presence,
                "partial_volume_tagging": len(presence) > 1,
                "duplicate_identities": duplicate_items,
                "unintended_gaps": unintended_gaps.get(key, []),
                "confirmed_source_gaps": sorted(intentional.get(key, set())),
            }
        )
    result["series_identity_audit"] = audit
    result["summary"] = {
        "archive_count": len(result["archives"]),
        "page_count": sum(int(item.get("page_count") or 0) for item in result["archives"]),
        "series_count": len(series_folders),
        "fallback_count": sum(1 for identity in identities if identity[1] == "volume"),
        "special_count": sum(1 for identity in identities if identity[1] == "special"),
        "unaffected_checked": len(expected_unaffected),
    }
    checksum_result, checksum_errors, checksum_warnings = _validate_checksums_manifest(library, required=require_checksums)
    result["checksums"] = checksum_result
    errors.extend(checksum_errors)
    warnings.extend(checksum_warnings)
    result["ok"] = not errors
    return result


def _deep_merge(base: Any, update: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(update, Mapping):
        merged = copy.deepcopy(dict(base))
        for key, value in update.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else copy.deepcopy(value)
        return merged
    return copy.deepcopy(update)


def _state_checksum(state: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(state))
    payload.pop("state_checksum", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_required_value(state: Mapping[str, Any], names: Sequence[str]) -> Any:
    return _first(state, names)


def _resume_state_errors(state: Mapping[str, Any], verify_checksum: bool = True) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    required_groups = {
        "schema_version": ("schema_version",),
        "run_id": ("run_id",),
        "lifecycle_status": ("status", "lifecycle_status"),
        "current_stage": ("current_stage", "current_phase"),
        "source_directory": ("source_root", "source_path", "source"),
        "formal_output_directory": ("formal_library", "final_library", "formal_path"),
        "backup_directory": ("backup_directory", "backup_root"),
        "persistent_staging": ("staging_path", "staging", "candidate_library"),
        "last_complete_unit": ("last_complete_unit", "last_completed_atomic_unit"),
        "completed_archives": ("completed_archives",),
        "pending_archives": ("pending_archives",),
        "source_hashes": ("source_hashes", "source_manifest"),
        "formal_library_baseline": ("formal_library_baseline", "formal_library_hashes"),
        "chapter_boundaries": ("chapter_boundaries", "confirmed_chapter_boundaries"),
        "ocr_permissions": ("ocr_permissions", "ocr_authorization"),
        "ocr_review_conclusions": ("ocr_review_conclusions", "ocr_visual_review"),
        "primary_editions": ("primary_editions", "primary_selection"),
        "ignored_damaged_items": ("ignored_damaged_items", "user_ignored_damaged_items"),
        "special_deduplication": ("special_deduplication", "special_dedupe_conclusions"),
        "locked_metadata": ("locked_metadata",),
        "candidate_library_status": ("candidate_library_status", "current_candidate_library_state"),
        "profile": ("profile",),
        "identity_policy": ("identity_policy",),
        "tool_fingerprint": ("tool_fingerprint", "tool_versions"),
        "config_sha256": ("config_sha256",),
        "config_path": ("config_path",),
        "plan_sha256": ("plan_sha256",),
        "plan_path": ("plan_path",),
        "decision_log_sha256": ("decision_log_sha256", "decision_resolution_sha256"),
        "decision_log_path": ("decision_log_path", "decision_resolution_path"),
        "lock_owner": ("lock_owner",),
        "filesystem_capabilities": ("filesystem_capabilities",),
        "candidate_manifest": ("candidate_manifest", "candidate_archive_manifest"),
        "review_copy_hashes": ("review_copy_hashes", "review_hashes"),
        "unaffected_archive_hashes": ("unaffected_archive_hashes",),
        "affected_formal_archives": ("affected_formal_archives",),
    }
    for label, names in required_groups.items():
        if _state_required_value(state, names) is None:
            errors.append(_issue("resume_state_field_missing", "resume state is missing a required field", label))
    if state.get("schema_version") != 1:
        errors.append(_issue("resume_state_schema_unsupported", "resume state schema_version must equal 1"))
    allowed_statuses = {
        "inventory", "preflight", "building-candidate", "paused", "candidate-ready",
        "validated-candidate", "promotion-intent", "rollback-pending", "rollback-complete",
        "rollback-failed", "validated-final",
    }
    status = _first(state, ("status", "lifecycle_status"))
    if not isinstance(status, str) or status not in allowed_statuses:
        errors.append(_issue("resume_state_status_invalid", "resume state lifecycle status is unsupported", status=status))
    if status == "validated-final":
        promotion = state.get("promotion")
        if not isinstance(promotion, Mapping) or promotion.get("formal_path_postcheck") is not True:
            errors.append(_issue("resume_state_terminal_incomplete", "validated-final requires a successful formal-path postcheck"))
        if not isinstance(state.get("promotion_journal"), str) or not state.get("promotion_journal", "").strip():
            errors.append(_issue("resume_state_terminal_incomplete", "validated-final requires a promotion journal path"))
        if state.get("candidate_library_status") != "validated-final":
            errors.append(_issue("resume_state_terminal_incomplete", "validated-final requires candidate_library_status=validated-final"))
    for label in ("completed_archives", "pending_archives"):
        if label in state and not isinstance(state[label], list):
            errors.append(_issue("resume_state_field_invalid", "resume state archive lists must be arrays", label))
    if "affected_formal_archives" in state and (
        not isinstance(state["affected_formal_archives"], list)
        or not all(isinstance(item, str) and _safe_relative(item) is not None for item in state["affected_formal_archives"])
    ):
        errors.append(_issue("resume_state_field_invalid", "affected_formal_archives must be an array of safe relative paths"))
    if "unaffected_archive_hashes" in state and not isinstance(state["unaffected_archive_hashes"], Mapping):
        errors.append(_issue("resume_state_field_invalid", "unaffected_archive_hashes must be a path-to-SHA-256 object"))
    for field in ("config_sha256", "plan_sha256", "decision_log_sha256"):
        value = state.get(field)
        if not isinstance(value, str) or not HASH_RE.fullmatch(value):
            errors.append(_issue("resume_state_field_invalid", "resume state fingerprint must be SHA-256", field))
    sequence = state.get("checkpoint_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        errors.append(_issue("resume_state_field_invalid", "checkpoint_sequence must be a positive integer", "checkpoint_sequence"))
    if verify_checksum:
        checksum = state.get("state_checksum")
        if not isinstance(checksum, str) or not HASH_RE.fullmatch(checksum):
            errors.append(_issue("resume_state_checksum_missing", "resume state checksum is missing or invalid"))
        elif checksum.casefold() != _state_checksum(state):
            errors.append(_issue("resume_state_checksum_mismatch", "resume state checksum does not match its content"))
    return errors


def _state_fingerprint_errors(state: Mapping[str, Any], parent: Path) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    checks = (
        ("config_path", "config_sha256"),
        ("plan_path", "plan_sha256"),
        ("decision_log_path", "decision_log_sha256"),
    )
    for path_field, hash_field in checks:
        raw = state.get(path_field)
        expected = state.get(hash_field)
        if not isinstance(raw, str) or not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
            continue
        path = Path(raw)
        if not path.is_absolute():
            safe = _safe_relative(raw)
            if safe is None:
                errors.append(_issue("resume_fingerprint_path_unsafe", "fingerprint path is unsafe", raw, field=path_field))
                continue
            path = parent / safe
        path = _absolute(path)
        if path.is_symlink() or not path.is_file():
            errors.append(_issue("resume_fingerprint_file_missing", "fingerprinted control file is missing or unsafe", path, field=path_field))
            continue
        actual = _sha256(path)
        if actual != expected.casefold():
            errors.append(_issue("resume_fingerprint_changed", "fingerprinted control file changed", path, field=path_field, expected=expected.casefold(), actual=actual))
    fingerprint = state.get("tool_fingerprint")
    if not isinstance(fingerprint, Mapping):
        errors.append(_issue("resume_tool_fingerprint_unverifiable", "tool_fingerprint must map script names to SHA-256 values"))
        return errors
    expected_tools = {
        "library_state.py": Path(__file__).resolve(),
        "cbz_transform.py": Path(__file__).resolve().parent / "cbz_transform.py",
    }
    for name, path in expected_tools.items():
        expected = fingerprint.get(name)
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
            errors.append(_issue("resume_tool_fingerprint_missing", "tool fingerprint is missing", name))
            continue
        if not path.is_file() or _sha256(path) != expected.casefold():
            errors.append(_issue("resume_tool_fingerprint_changed", "tool script changed since checkpoint", path, expected=expected))
    return errors


def _prepare_state(state: Mapping[str, Any], previous_sequence: int = 0) -> Dict[str, Any]:
    prepared = copy.deepcopy(dict(state))
    prepared.setdefault("schema_version", 1)
    prepared["checkpoint_sequence"] = previous_sequence + 1
    prepared["updated_at"] = datetime.now(timezone.utc).isoformat()
    prepared["state_checksum"] = _state_checksum(prepared)
    return prepared


def _atomic_write_json(path: Path, value: Any) -> None:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("state parent directory does not exist: {}".format(parent))
    if path.is_symlink():
        raise ValueError("refusing to replace a symlink state path: {}".format(path))
    descriptor, temporary_name = tempfile.mkstemp(prefix=".{}-".format(path.name), suffix=".tmp", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), 0o600)
        os.replace(str(temporary), str(path))
        try:
            directory_fd = os.open(str(parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def checkpoint_state(
    state_path: Path,
    state_value: Optional[Any],
    update_value: Optional[Any],
    update: bool,
    execute: bool,
) -> Dict[str, Any]:
    state_path = _absolute(state_path)
    errors: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "command": "checkpoint", "state": str(state_path), "dry_run": not execute,
        "ok": False, "written": False, "errors": errors,
    }
    exists = state_path.exists()
    if state_path.is_symlink():
        errors.append(_issue("state_symlink_refused", "state path must not be a symlink", state_path))
        return result
    if exists and not state_path.is_file():
        errors.append(_issue("state_not_file", "state path exists but is not a regular file", state_path))
        return result
    if exists and not update:
        errors.append(_issue("state_overwrite_refused", "existing state requires the explicit --update flag", state_path))
        return result
    if not exists and update and state_value is None:
        errors.append(_issue("state_update_missing_base", "cannot apply only update JSON when the state file does not exist", state_path))
        return result
    if state_value is not None and update_value is not None:
        errors.append(_issue("ambiguous_state_payload", "use state JSON or update JSON, not both"))
        return result
    payload = update_value if update_value is not None else state_value
    if payload is None:
        errors.append(_issue("state_payload_missing", "checkpoint requires --state-json/--json or --update-json"))
        return result
    if not isinstance(payload, Mapping):
        errors.append(_issue("state_payload_not_object", "resume state JSON must be an object"))
        return result
    try:
        if exists:
            current = _read_json_file(state_path, "state")
            if not isinstance(current, Mapping):
                raise ValueError("existing state JSON must be an object")
            current_errors = _resume_state_errors(current)
            if current_errors:
                errors.extend(current_errors)
                result["existing_state_rejected"] = True
                return result
            proposed = _deep_merge(current, payload)
            previous_sequence = current.get("checkpoint_sequence", 0)
        else:
            proposed = copy.deepcopy(dict(payload))
            previous_sequence = 0
    except ValueError as exc:
        errors.append(_issue("state_read_failed", str(exc), state_path))
        return result
    proposed = _prepare_state(proposed, previous_sequence if isinstance(previous_sequence, int) else 0)
    state_errors = _resume_state_errors(proposed) + _state_fingerprint_errors(proposed, state_path.parent)
    if state_errors:
        errors.extend(state_errors)
        result["proposed_state"] = proposed
        return result
    result["proposed_state"] = proposed
    if execute:
        try:
            _atomic_write_json(state_path, proposed)
            result["written"] = True
        except (OSError, ValueError) as exc:
            errors.append(_issue("state_write_failed", str(exc), state_path))
            return result
    result["ok"] = True
    return result


def _state_path(value: Any, state_parent: Path, keys: Sequence[str]) -> Optional[Path]:
    if not isinstance(value, Mapping):
        return None
    raw = _first(value, keys)
    if isinstance(raw, Mapping):
        raw = _first(raw, ("path", "root", "library"))
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    return _absolute(path if path.is_absolute() else state_parent / path)


def _verify_manifest(
    records: Sequence[Tuple[str, str, Optional[Path]]],
    fallback_root: Path,
    kind: str,
) -> Tuple[List[Dict[str, Any]], int]:
    errors: List[Dict[str, Any]] = []
    checked = 0
    for raw, expected, root in sorted(records, key=lambda row: (str(row[2] or ""), row[0])):
        path = _resolve_record_path(raw, root, fallback_root)
        if path is None:
            errors.append(_issue("unsafe_{}_path".format(kind), "recorded hash path is unsafe", raw))
            continue
        if not path.is_file():
            errors.append(_issue("{}_file_missing".format(kind), "recorded file is missing", path))
            continue
        try:
            actual = _sha256(path)
        except OSError as exc:
            errors.append(_issue("{}_hash_read_failed".format(kind), str(exc), path))
            continue
        checked += 1
        if actual != expected:
            errors.append(_issue("{}_hash_changed".format(kind), "SHA-256 differs from the resume baseline", path, expected=expected, actual=actual))
    return errors, checked


def _state_hash_records(state: Mapping[str, Any], names: Sequence[str], root: Optional[Path]) -> List[Tuple[str, str, Optional[Path]]]:
    records: List[Tuple[str, str, Optional[Path]]] = []
    for value in _named_values(state, names):
        records.extend(_hash_records(value, root))
    dedup: Dict[Tuple[str, str], Tuple[str, str, Optional[Path]]] = {}
    for item in records:
        dedup[(str(item[2] or ""), item[0])] = item
    return list(dedup.values())


def resume_check(
    state_path: Path,
    source_override: Optional[Path] = None,
    formal_override: Optional[Path] = None,
    staging_override: Optional[Path] = None,
) -> Dict[str, Any]:
    state_path = _absolute(state_path)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "command": "resume-check", "state": str(state_path), "ok": False,
        "errors": errors, "warnings": warnings, "actions": actions,
        "repeat_decisions": False,
    }
    if state_path.is_symlink() or not state_path.is_file():
        errors.append(_issue("state_missing_or_unsafe", "state must be an existing regular non-symlink file", state_path))
        return result
    try:
        state = _read_json_file(state_path, "state")
    except ValueError as exc:
        errors.append(_issue("state_read_failed", str(exc), state_path))
        return result
    if not isinstance(state, Mapping):
        errors.append(_issue("state_not_object", "resume state JSON must be an object", state_path))
        return result
    parent = state_path.parent
    state_schema_errors = _resume_state_errors(state)
    errors.extend(state_schema_errors)
    source_root = _absolute(source_override) if source_override else _state_path(
        state, parent, ("source_root", "source_path", "source")
    )
    formal_root = _absolute(formal_override) if formal_override else _state_path(
        state, parent, ("formal_library", "final_library", "formal_path")
    )
    staging = _absolute(staging_override) if staging_override else _state_path(
        state, parent, ("staging_path", "staging", "candidate", "candidate_library")
    )
    fingerprint_state: Mapping[str, Any] = state
    if staging is not None and not staging.exists() and formal_root is not None and formal_root.is_dir():
        fingerprint_state = _relocate_control_paths(state, staging, formal_root)
    errors.extend(_state_fingerprint_errors(fingerprint_state, parent))
    source_records = _state_hash_records(
        state, ("source_hashes", "source_manifest", "source_files", "sources"), source_root
    )
    formal_records = _state_hash_records(
        state, ("formal_library_baseline", "formal_library_hashes", "baseline_hashes"), formal_root
    )
    if not source_records:
        errors.append(_issue("source_baseline_missing", "resume state has no source SHA-256 manifest"))
    else:
        source_errors, source_checked = _verify_manifest(source_records, source_root or parent, "source")
        errors.extend(source_errors)
        result["source_hashes_checked"] = source_checked
    formal_baseline_value = state.get("formal_library_baseline")
    empty_formal_baseline = (
        isinstance(formal_baseline_value, Mapping)
        and isinstance(formal_baseline_value.get("archives"), Mapping)
        and not formal_baseline_value.get("archives")
    )
    if formal_records:
        formal_errors, formal_checked = _verify_manifest(formal_records, formal_root or parent, "formal")
        errors.extend(formal_errors)
        result["formal_hashes_checked"] = formal_checked
    elif formal_root is not None and formal_root.exists():
        errors.append(_issue("formal_baseline_missing", "existing formal library has no formal-library hash baseline"))
    elif formal_root is not None and not empty_formal_baseline:
        errors.append(_issue("formal_baseline_missing", "new formal path requires an explicit empty formal-library baseline"))

    decisions: Dict[str, Any] = {}
    for key in (
        "decisions", "resolved_decisions", "decision_resolution", "ocr_permissions",
        "ocr_review_conclusions", "chapter_boundaries", "primary_editions", "ignored_damaged_items",
        "special_deduplication", "visual_review", "locked_metadata",
    ):
        if key in state:
            decisions[key] = state[key]
    result["reused_decision_sections"] = sorted(decisions.keys())

    journal_raw = state.get("promotion_journal")
    journal_path = Path(journal_raw) if isinstance(journal_raw, str) else parent / "promotion-journal.json"
    if not journal_path.is_absolute():
        journal_path = parent / journal_path
    journal_path = _absolute(journal_path)
    terminal_validated = str(state.get("status", "")).casefold() == "validated-final"
    if terminal_validated and (journal_path.is_symlink() or not journal_path.is_file()):
        errors.append(_issue("validated_journal_missing", "validated-final requires its promotion journal for backup verification", journal_path))
    if journal_path.is_file() and not journal_path.is_symlink():
        try:
            journal = _read_json_file(journal_path, "promotion journal")
        except ValueError as exc:
            errors.append(_issue("promotion_journal_invalid", str(exc), journal_path))
        else:
            status = journal.get("status") if isinstance(journal, Mapping) else None
            result["promotion_journal_status"] = status
            if isinstance(journal, Mapping):
                if journal.get("run_id") != state.get("run_id"):
                    errors.append(_issue("promotion_journal_run_mismatch", "promotion journal belongs to a different run", journal_path))
                expected_journal_checksum = journal.get("journal_checksum")
                actual_journal_checksum = _state_checksum(
                    {key: value for key, value in journal.items() if key != "journal_checksum"}
                )
                if not isinstance(expected_journal_checksum, str) or expected_journal_checksum != actual_journal_checksum:
                    errors.append(_issue("promotion_journal_checksum_mismatch", "promotion journal checksum is missing or invalid", journal_path))
                candidate_path = Path(str(journal.get("candidate", "")))
                formal_path = Path(str(journal.get("formal", "")))
                backup_raw = journal.get("backup")
                backup_path = Path(backup_raw) if isinstance(backup_raw, str) and backup_raw else None
                if formal_root is not None and _absolute(formal_path) != formal_root:
                    errors.append(_issue("promotion_journal_formal_mismatch", "promotion journal formal path differs from resume state", journal_path))
                expected_candidate = journal.get("candidate_archive_manifest")
                expected_formal = journal.get("formal_before_manifest")
                expected_backup = journal.get("backup_manifest", expected_formal)
                observations: Dict[str, Any] = {
                    "candidate_exists": candidate_path.is_dir(),
                    "formal_exists": formal_path.is_dir(),
                    "backup_exists": bool(backup_path and backup_path.is_dir()),
                    "candidate_matches": False,
                    "formal_matches_candidate": False,
                    "formal_matches_old": False,
                    "backup_matches_old": False,
                }
                try:
                    if candidate_path.is_dir() and isinstance(expected_candidate, Mapping):
                        observations["candidate_matches"] = _archive_manifest(candidate_path) == dict(expected_candidate)
                    if formal_path.is_dir():
                        if isinstance(expected_candidate, Mapping):
                            observations["formal_matches_candidate"] = _archive_manifest(formal_path) == dict(expected_candidate)
                        if isinstance(expected_formal, Mapping):
                            observations["formal_matches_old"] = _tree_manifest(formal_path) == dict(expected_formal)
                    if backup_path and backup_path.is_dir() and isinstance(expected_backup, Mapping):
                        observations["backup_matches_old"] = _tree_manifest(backup_path) == dict(expected_backup)
                except (OSError, ValueError) as exc:
                    errors.append(_issue("promotion_recovery_manifest_failed", str(exc), journal_path))
                if status == "validated-final" and isinstance(expected_formal, Mapping) and expected_formal:
                    if not observations["backup_exists"] or not observations["backup_matches_old"]:
                        errors.append(_issue("validated_backup_missing_or_changed", "validated-final backup is missing or differs from its journal manifest", backup_path or "<missing>"))
                if terminal_validated:
                    if status != "validated-final":
                        errors.append(_issue("validated_journal_status_mismatch", "validated-final state requires a validated-final journal", journal_path, status=status))
                    if not observations["formal_matches_candidate"]:
                        errors.append(_issue("validated_journal_formal_changed", "formal library differs from the journal candidate manifest", formal_path))
                if status not in {"validated-final", "rollback-complete"}:
                    new_library = isinstance(expected_formal, Mapping) and not expected_formal
                    if new_library and (not observations["formal_exists"]) and observations["candidate_matches"] and not observations["backup_exists"]:
                        recovery_action = "continue-candidate-promotion-then-postcheck"
                    elif new_library and observations["formal_matches_candidate"] and not observations["backup_exists"]:
                        recovery_action = "run-formal-postcheck-and-finalize"
                    elif observations["formal_matches_old"] and observations["candidate_matches"] and not observations["backup_exists"]:
                        recovery_action = "close-noop-intent-and-restart-promotion"
                    elif (not observations["formal_exists"]) and observations["candidate_matches"] and observations["backup_matches_old"]:
                        recovery_action = "continue-candidate-promotion-then-postcheck"
                    elif observations["formal_matches_candidate"] and observations["backup_matches_old"]:
                        recovery_action = "run-formal-postcheck-and-finalize"
                    elif observations["formal_matches_old"] and not observations["backup_exists"]:
                        recovery_action = "mark-rollback-complete"
                    else:
                        recovery_action = "stop-for-manual-recovery"
                    result["promotion_recovery"] = {"action": recovery_action, "observations": observations}
            if status not in {"validated-final", "rollback-complete"}:
                errors.append(_issue("promotion_journal_incomplete", "promotion journal requires reconciliation before normal resume", journal_path, status=status))
                actions.append({
                    "action": result.get("promotion_recovery", {}).get("action", "stop-for-manual-recovery"),
                    "path": str(journal_path),
                    "status": status,
                    "execute_with": "recover-promotion",
                })

    if not terminal_validated and staging is not None and staging.is_dir() and not staging.is_symlink():
        candidate_records = _hash_records(state.get("candidate_manifest"), staging)
        if not candidate_records:
            errors.append(_issue("candidate_manifest_missing", "existing persistent staging lacks a recorded candidate manifest", staging))
        else:
            candidate_errors, candidate_checked = _verify_manifest(candidate_records, staging, "candidate")
            errors.extend(candidate_errors)
            result["candidate_hashes_checked"] = candidate_checked

    blocking_hash_errors = bool(errors)
    if blocking_hash_errors:
        actions.append({"action": "stop-and-replan", "reason": "recorded source or formal-library baseline changed"})
    elif terminal_validated:
        actions.append({"action": "already-validated-final", "formal_library": str(formal_root) if formal_root else None})
    elif staging is None:
        errors.append(_issue("staging_path_missing", "resume state does not record a staging path"))
        actions.append({"action": "stop", "reason": "staging path is not recorded"})
    elif staging.is_dir() and not staging.is_symlink():
        actions.append({
            "action": "continue-from-last-complete-unit",
            "staging": str(staging),
            "last_complete_unit": state.get("last_complete_unit"),
            "reuse_recorded_decisions": True,
        })
    elif staging.exists():
        errors.append(_issue("staging_path_unsafe", "recorded staging path exists but is not a regular directory", staging))
        actions.append({"action": "stop", "reason": "staging path is unsafe"})
    else:
        actions.append({
            "action": "rebuild-staging-from-checkpoint",
            "staging": str(staging),
            "reuse_recorded_decisions": True,
            "rebuild_only_completed_outputs_from_recorded_manifests": True,
        })

    cache_values: List[str] = []
    for value in _named_values(state, ("disposable_caches", "reconstructable_caches", "cache_paths")):
        if isinstance(value, str):
            cache_values.append(value)
        elif isinstance(value, list):
            cache_values.extend(str(item) for item in value if isinstance(item, (str, Path)))
    for raw in sorted(set(cache_values)):
        cache = Path(raw)
        cache = _absolute(cache if cache.is_absolute() else parent / cache)
        if not cache.exists():
            actions.append({"action": "rebuild-disposable-cache", "path": str(cache)})
    result["actions"] = actions
    result["ok"] = not errors
    return result


def _archive_manifest(library: Path) -> Dict[str, str]:
    return {path.relative_to(library).as_posix(): _sha256(path) for path in _archive_paths(library)}


def _formal_partition(
    state: Mapping[str, Any], formal_manifest: Mapping[str, str], candidate_manifest: Mapping[str, str]
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    raw_unaffected = state.get("unaffected_archive_hashes")
    raw_affected = state.get("affected_formal_archives")
    unaffected: Dict[str, str] = {}
    if isinstance(raw_unaffected, Mapping):
        for raw_path, raw_digest in raw_unaffected.items():
            if not isinstance(raw_path, str) or _safe_relative(raw_path) is None:
                errors.append(_issue("unaffected_partition_invalid", "unaffected archive path is unsafe", raw_path))
                continue
            if not isinstance(raw_digest, str) or not HASH_RE.fullmatch(raw_digest):
                errors.append(_issue("unaffected_partition_invalid", "unaffected archive SHA-256 is invalid", raw_path))
                continue
            unaffected[PurePosixPath(raw_path).as_posix()] = raw_digest.casefold()
    else:
        errors.append(_issue("unaffected_partition_missing", "promotion state must explicitly record unaffected archive hashes"))
    affected = {
        PurePosixPath(item).as_posix()
        for item in raw_affected
        if isinstance(raw_affected, list) and isinstance(item, str) and _safe_relative(item) is not None
    } if isinstance(raw_affected, list) else set()
    if not isinstance(raw_affected, list):
        errors.append(_issue("affected_partition_missing", "promotion state must explicitly record affected formal archives"))
    formal_paths = set(formal_manifest)
    if set(unaffected) & affected:
        errors.append(_issue("formal_partition_overlap", "one formal archive is marked both affected and unaffected", archives=sorted(set(unaffected) & affected)))
    if set(unaffected) | affected != formal_paths:
        errors.append(_issue(
            "formal_partition_incomplete",
            "affected and unaffected records must partition every pre-promotion formal archive exactly once",
            missing=sorted(formal_paths - (set(unaffected) | affected)),
            unexpected=sorted((set(unaffected) | affected) - formal_paths),
        ))
    for relative, digest in sorted(unaffected.items()):
        if formal_manifest.get(relative) != digest:
            errors.append(_issue("unaffected_baseline_mismatch", "unaffected hash differs from the verified formal baseline", relative))
        if candidate_manifest.get(relative) != digest:
            errors.append(_issue("unaffected_archive_changed", "unaffected archive is missing or changed in the candidate", relative))
    return unaffected, errors


def _runtime_backup_name(final: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return "{}.backup-{}".format(final.name, stamp)


def _safe_backup_name(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and Path(name).name == name and "\x00" not in name


def _promotion_state_target(state: Path, candidate: Path, final: Path) -> Path:
    try:
        relative = state.relative_to(candidate)
        return final / relative
    except ValueError:
        pass
    try:
        relative = state.relative_to(final)
        return final / relative
    except ValueError:
        return state


def _promoted_path(path: Path, candidate: Path, final: Path) -> Path:
    try:
        return final / path.relative_to(candidate)
    except ValueError:
        return path


def _relocate_control_paths(state: Mapping[str, Any], candidate: Path, final: Path) -> Dict[str, Any]:
    relocated = copy.deepcopy(dict(state))
    for field in ("config_path", "plan_path", "decision_log_path"):
        raw = relocated.get(field)
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if path.is_absolute():
            relocated[field] = str(_promoted_path(_absolute(path), candidate, final))
    return relocated


def _journal_value(base: Mapping[str, Any], status: str, **updates: Any) -> Dict[str, Any]:
    value = _deep_merge(base, updates)
    value["schema_version"] = 1
    value["status"] = status
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    value["journal_checksum"] = _state_checksum({key: item for key, item in value.items() if key != "journal_checksum"})
    return value


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write_json(path, value)


def _special_audit_errors(
    validation: Mapping[str, Any], state: Mapping[str, Any], state_parent: Path
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    special_archives = {
        str(item.get("path")): item
        for item in validation.get("archives", [])
        if isinstance(item, Mapping)
        and str(item.get("format") or "").casefold() == "special"
    }
    if not special_archives:
        return errors
    source_root = _state_path(state, state_parent, ("source_root", "source_path", "source"))
    review_root = _state_path(state, state_parent, ("review_root", "needs_review_root"))
    state_review_records = _state_hash_records(state, ("review_copy_hashes", "review_hashes"), review_root)
    verified_review: Dict[str, str] = {}
    for raw_path, digest, root in state_review_records:
        resolved = _resolve_record_path(raw_path, root, review_root or state_parent)
        if resolved is not None:
            verified_review[_locator_key(resolved)] = digest
    raw = state.get("special_deduplication")
    records = list(_walk_dicts(raw)) if isinstance(raw, (Mapping, list)) else []
    matched: Set[str] = set()
    for record in records:
        output = _record_path(record)
        if not output:
            continue
        normalized = output.lstrip("./")
        candidates = {path for path in special_archives if path == normalized}
        if not candidates:
            continue
        output_hashes = list(special_archives[normalized].get("page_sha256", []))
        output_perceptual = list(special_archives[normalized].get("page_perceptual_hash", []))
        if record.get("result") not in {"complete", "not-applicable", "no-duplicates", "resolved"}:
            errors.append(_issue("special_deduplication_incomplete", "Special deduplication state is incomplete", normalized))
        if record.get("visual_review_complete") is not True:
            errors.append(_issue("special_deduplication_incomplete", "Special visual review is incomplete", normalized))
        if not isinstance(record.get("audit_record_id"), str) or not record.get("audit_record_id", "").strip():
            errors.append(_issue("special_deduplication_incomplete", "Special audit record ID is missing", normalized))
        source_hashes = record.get("source_hashes")
        source_records = _hash_records(source_hashes, source_root)
        verified_sources: Dict[str, str] = {}
        if not source_records:
            errors.append(_issue("special_deduplication_incomplete", "Special source hashes are missing", normalized))
        else:
            source_errors, checked = _verify_manifest(source_records, source_root or state_parent, "special_source")
            if source_errors or checked != len(source_records):
                errors.append(_issue("special_deduplication_incomplete", "Special source hashes do not match preserved sources", normalized, source_errors=source_errors))
            for raw_path, digest, root in source_records:
                resolved = _resolve_record_path(raw_path, root, source_root or state_parent)
                if resolved is not None:
                    verified_sources[_locator_key(resolved)] = digest
        omitted = record.get("omitted_pages")
        if not isinstance(omitted, list):
            errors.append(_issue("special_deduplication_incomplete", "Special omitted-page audit must be an array", normalized))
        else:
            for index, page in enumerate(omitted):
                if not isinstance(page, Mapping):
                    errors.append(_issue("special_deduplication_incomplete", "Special omitted-page row is invalid", normalized, index=index))
                    continue
                source_value = page.get("source")
                source_page = page.get("source_page")
                source_page_hash = page.get("source_page_sha256", page.get("byte_sha256"))
                target = page.get("duplicate_target", page.get("dedupe_target"))
                evidence = page.get("evidence")
                if not isinstance(source_value, str) or not isinstance(source_page, int) or isinstance(source_page, bool):
                    errors.append(_issue("special_deduplication_incomplete", "omitted page lacks a source archive and page number", normalized, index=index))
                    continue
                source_path = _absolute(Path(source_value) if Path(source_value).is_absolute() else (source_root or state_parent) / source_value)
                if verified_sources.get(_locator_key(source_path)) is None:
                    errors.append(_issue(
                        "special_deduplication_incomplete",
                        "omitted page source is not in the verified source manifest",
                        normalized,
                        index=index,
                        source=str(source_path),
                        verified_sources=sorted(verified_sources),
                    ))
                    continue
                source_pages, source_error = _verified_source_page_evidence(source_path)
                if source_error is not None or source_pages is None or source_page < 1 or source_page > len(source_pages):
                    errors.append(_issue("special_deduplication_incomplete", "omitted source page cannot be verified", normalized, index=index))
                    continue
                actual_source = source_pages[source_page - 1]
                if not isinstance(source_page_hash, str) or source_page_hash.casefold() != actual_source["sha256"]:
                    errors.append(_issue("special_deduplication_incomplete", "omitted source-page SHA-256 differs from preserved bytes", normalized, index=index))
                if not isinstance(target, Mapping):
                    errors.append(_issue("special_deduplication_incomplete", "omitted page lacks a structured duplicate target", normalized, index=index))
                    continue
                target_page = target.get("output_page")
                target_hash = target.get("output_sha256", target.get("byte_sha256"))
                if not isinstance(target_page, int) or isinstance(target_page, bool) or target_page < 1 or target_page > len(output_hashes):
                    errors.append(_issue("special_deduplication_incomplete", "duplicate target page is outside the actual Special", normalized, index=index))
                    continue
                actual_target_hash = output_hashes[target_page - 1]
                if not isinstance(target_hash, str) or target_hash.casefold() != actual_target_hash:
                    errors.append(_issue("special_deduplication_incomplete", "duplicate target SHA-256 differs from the actual Special page", normalized, index=index))
                method = evidence.get("method") if isinstance(evidence, Mapping) else None
                if method == "byte-sha256":
                    if actual_source["sha256"] != actual_target_hash or str(evidence.get("sha256", "")).casefold() != actual_source["sha256"]:
                        errors.append(_issue("special_deduplication_incomplete", "exact duplicate evidence differs from actual source and target bytes", normalized, index=index))
                elif method in {"dhash-88-color", "perceptual-hash"}:
                    actual_target_perceptual: Optional[str] = None
                    try:
                        actual_target_perceptual = output_perceptual[target_page - 1]
                        actual_distance = _hamming_hex(actual_source["perceptual_hash"], actual_target_perceptual)
                    except (TypeError, ValueError):
                        actual_distance = -1
                    review = evidence.get("visual_review") if isinstance(evidence, Mapping) else None
                    if (
                        actual_distance != evidence.get("hamming_distance")
                        or str(target.get("perceptual_hash", "")) != str(actual_target_perceptual)
                        or not isinstance(review, Mapping)
                    ):
                        errors.append(_issue("special_deduplication_incomplete", "perceptual duplicate evidence is not bound to actual page hashes", normalized, index=index))
                    elif (
                        review.get("duplicate") is not True
                        or str(review.get("source_page_sha256", "")).casefold() != actual_source["sha256"]
                        or str(review.get("duplicate_target_sha256", "")).casefold() != actual_target_hash
                        or not isinstance(review.get("reviewer"), str) or not review.get("reviewer", "").strip()
                        or not isinstance(review.get("reason"), str) or not review.get("reason", "").strip()
                    ):
                        errors.append(_issue("special_deduplication_incomplete", "perceptual duplicate lacks a hash-bound positive visual decision", normalized, index=index))
                else:
                    errors.append(_issue("special_deduplication_incomplete", "omitted page evidence method is unsupported", normalized, index=index))
                review_copy_value = page.get("review_copy")
                review_copy_hash = page.get("review_copy_sha256")
                if page.get("review_copy_preserved") is not True or not isinstance(review_copy_value, str):
                    errors.append(_issue("special_deduplication_incomplete", "omitted page lacks a preserved review copy", normalized, index=index))
                else:
                    review_path = _absolute(Path(review_copy_value) if Path(review_copy_value).is_absolute() else (review_root or state_parent) / review_copy_value)
                    expected_review_hash = verified_review.get(_locator_key(review_path))
                    if (
                        expected_review_hash is None
                        or not isinstance(review_copy_hash, str)
                        or review_copy_hash.casefold() != expected_review_hash
                        or review_path.is_symlink() or not review_path.is_file()
                        or _sha256(review_path) != expected_review_hash
                    ):
                        errors.append(_issue("special_deduplication_incomplete", "review copy is not bound to the verified review manifest", normalized, index=index))
        if record.get("review_copy_status") != ("preserved" if isinstance(omitted, list) and omitted else "not-applicable"):
            errors.append(_issue("special_deduplication_incomplete", "Special review-copy status is missing", normalized))
        matched.update(candidates)
    for path in sorted(set(special_archives) - matched):
        errors.append(_issue("special_deduplication_missing", "formal Special lacks persisted deduplication/provenance state", path))
    return errors


def promote_library(
    candidate: Path,
    final: Path,
    backup_root: Path,
    state_path: Path,
    execute: bool,
    plan: Optional[Any] = None,
    baseline: Optional[Any] = None,
    plan_base: Optional[Path] = None,
    baseline_base: Optional[Path] = None,
    backup_name: Optional[str] = None,
    plan_file: Optional[Path] = None,
) -> Dict[str, Any]:
    candidate = _absolute(candidate)
    final = _absolute(final)
    backup_root = _absolute(backup_root)
    state_path = _absolute(state_path)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "command": "promote", "candidate": str(candidate), "final": str(final),
        "backup_root": str(backup_root), "state": str(state_path),
        "dry_run": not execute, "executed": False, "rolled_back": False,
        "failed_candidate_preserved": False, "ok": False,
        "errors": errors, "warnings": warnings,
    }
    for label, path in (("candidate", candidate), ("final", final), ("backup_root", backup_root), ("state", state_path)):
        if path.is_symlink():
            errors.append(_issue("{}_symlink_refused".format(label), "promotion paths must not be symlinks", path))
    if errors:
        return result
    if not candidate.is_dir():
        errors.append(_issue("candidate_missing", "candidate must be an existing directory", candidate))
    if not final.parent.is_dir():
        errors.append(_issue("final_parent_missing", "final parent must be an existing directory", final.parent))
    if not backup_root.is_dir():
        errors.append(_issue("backup_root_missing", "backup root must be an existing directory", backup_root))
    if not state_path.is_file():
        errors.append(_issue("state_missing", "promotion state must be an existing regular file", state_path))
    if candidate == final or _path_within(candidate, final) or _path_within(final, candidate):
        errors.append(_issue("overlapping_promotion_paths", "candidate and final must be distinct, non-nested directories"))
    if _path_within(backup_root, candidate) or _path_within(backup_root, final) or _path_within(candidate, backup_root) or _path_within(final, backup_root):
        errors.append(_issue("unsafe_backup_location", "backup root must be outside candidate and final trees", backup_root))
    if final.exists() and not final.is_dir():
        errors.append(_issue("final_not_directory", "existing final path is not a directory", final))
    if errors:
        return result
    errors.extend(_tree_safety_errors(candidate))
    if final.exists():
        errors.extend(_tree_safety_errors(final))
    if errors:
        return result
    try:
        state = _read_json_file(state_path, "state")
        if not isinstance(state, Mapping):
            raise ValueError("promotion state JSON must be an object")
    except ValueError as exc:
        errors.append(_issue("state_read_failed", str(exc), state_path))
        return result

    errors.extend(_resume_state_errors(state))
    errors.extend(_state_fingerprint_errors(state, state_path.parent))
    recorded_plan = Path(str(state.get("plan_path")))
    if not recorded_plan.is_absolute():
        recorded_plan = state_path.parent / recorded_plan
    recorded_plan = _absolute(recorded_plan)
    if plan_file is not None and _absolute(plan_file) != recorded_plan:
        errors.append(_issue("promotion_plan_mismatch", "--plan must be the exact plan fingerprinted by resume state", plan_file, recorded_plan=str(recorded_plan)))
    try:
        recorded_plan_value = _read_json_file(recorded_plan, "recorded plan")
    except ValueError as exc:
        errors.append(_issue("promotion_plan_failed", str(exc), recorded_plan))
    else:
        if plan is not None and plan != recorded_plan_value:
            errors.append(_issue("promotion_plan_mismatch", "supplied plan content differs from the fingerprinted state plan", recorded_plan))
        plan = recorded_plan_value
        plan_base = recorded_plan.parent
    source_root = _state_path(state, state_path.parent, ("source_root", "source_path", "source"))
    source_records = _state_hash_records(state, ("source_hashes", "source_manifest", "source_files"), source_root)
    if not source_records:
        errors.append(_issue("source_baseline_missing", "promotion state has no source SHA-256 records"))
    else:
        source_errors, source_checked = _verify_manifest(source_records, source_root or state_path.parent, "source")
        errors.extend(source_errors)
        result["source_hashes_checked"] = source_checked
    review_root = _state_path(state, state_path.parent, ("review_root", "needs_review_root"))
    review_records = _state_hash_records(state, ("review_copy_hashes", "review_hashes"), review_root)
    if review_records:
        review_errors, review_checked = _verify_manifest(review_records, review_root or state_path.parent, "review")
        errors.extend(review_errors)
        result["review_hashes_checked"] = review_checked
    if final.exists():
        formal_baseline_value = state.get("formal_library_baseline")
        formal_root_value = formal_baseline_value.get("root") if isinstance(formal_baseline_value, Mapping) else None
        if not isinstance(formal_root_value, str) or _absolute(Path(formal_root_value)) != final:
            errors.append(_issue("formal_baseline_root_mismatch", "formal baseline root must equal --final", final, baseline_root=formal_root_value))
        formal_records = _state_hash_records(
            state, ("formal_library_baseline", "formal_library_hashes", "baseline_hashes"), final
        )
        if not formal_records:
            errors.append(_issue("formal_baseline_missing", "existing formal library requires a recorded SHA-256 baseline", final))
        else:
            formal_errors, formal_checked = _verify_manifest(formal_records, final, "formal")
            errors.extend(formal_errors)
            result["formal_hashes_checked"] = formal_checked
    else:
        formal_baseline_value = state.get("formal_library_baseline")
        formal_root_value = formal_baseline_value.get("root") if isinstance(formal_baseline_value, Mapping) else None
        formal_archives_value = formal_baseline_value.get("archives") if isinstance(formal_baseline_value, Mapping) else None
        if (
            not isinstance(formal_root_value, str)
            or _absolute(Path(formal_root_value)) != final
            or not isinstance(formal_archives_value, Mapping)
            or bool(formal_archives_value)
        ):
            errors.append(_issue("formal_baseline_new_library_invalid", "new formal path requires an explicit empty baseline rooted at --final", final))
    state_staging = _state_path(state, state_path.parent, ("staging_path", "staging", "candidate_library"))
    candidate_manifest_value = state.get("candidate_manifest")
    manifest_root = None
    if isinstance(candidate_manifest_value, Mapping) and isinstance(candidate_manifest_value.get("root"), str):
        manifest_root = _absolute(Path(str(candidate_manifest_value["root"])))
    if state_staging != candidate or manifest_root != candidate:
        errors.append(_issue(
            "candidate_manifest_root_mismatch",
            "--candidate must equal both the checkpoint staging path and candidate_manifest.root",
            candidate,
            staging=str(state_staging) if state_staging else None,
            manifest_root=str(manifest_root) if manifest_root else None,
        ))
    approved_candidate_records = _hash_records(candidate_manifest_value, candidate)
    actual_candidate_manifest: Dict[str, str] = {}
    if not approved_candidate_records:
        errors.append(_issue("candidate_manifest_missing", "promotion state has no approved candidate archive manifest", candidate))
    else:
        approved_errors, approved_checked = _verify_manifest(approved_candidate_records, candidate, "candidate")
        errors.extend(approved_errors)
        result["approved_candidate_hashes_checked"] = approved_checked
        approved_paths = {
            Path(raw).as_posix() if not Path(raw).is_absolute() else Path(raw).resolve().relative_to(candidate.resolve()).as_posix()
            for raw, _digest, _root in approved_candidate_records
            if (not Path(raw).is_absolute()) or _path_within(Path(raw).resolve(), candidate.resolve())
        }
        actual_candidate_manifest = _archive_manifest(candidate)
        actual_candidate_paths = set(actual_candidate_manifest)
        if approved_paths != actual_candidate_paths:
            errors.append(_issue(
                "candidate_manifest_set_changed",
                "current candidate archive set differs from the approved checkpoint manifest",
                candidate,
                missing=sorted(approved_paths - actual_candidate_paths),
                added=sorted(actual_candidate_paths - approved_paths),
            ))
    formal_manifest = _archive_manifest(final) if final.exists() else {}
    unaffected, partition_errors = _formal_partition(state, formal_manifest, actual_candidate_manifest)
    errors.extend(partition_errors)
    supplied_unaffected = _unaffected_expectations(
        plan, baseline, candidate, baseline_base or state_path.parent
    ) if baseline is not None else unaffected
    if supplied_unaffected != unaffected:
        errors.append(_issue(
            "promotion_baseline_mismatch",
            "--baseline must exactly match the unaffected archive partition fingerprinted in resume state",
            expected=unaffected,
            supplied=supplied_unaffected,
        ))
    baseline = {"unaffected_archives": unaffected}
    baseline_base = candidate
    result["unaffected_hashes_checked"] = len(unaffected)
    journal_raw = state.get("promotion_journal")
    journal_path = Path(journal_raw) if isinstance(journal_raw, str) else state_path.parent / "promotion-journal.json"
    if not journal_path.is_absolute():
        journal_path = state_path.parent / journal_path
    journal_path = _absolute(journal_path)
    if journal_path.exists():
        errors.append(_issue("promotion_journal_conflict", "refusing to overwrite an existing promotion journal", journal_path))
    result["promotion_journal"] = str(journal_path)
    if errors:
        return result

    chosen_name = backup_name or _runtime_backup_name(final)
    if not _safe_backup_name(chosen_name) or re.search(r"\d{8}", chosen_name) is None:
        errors.append(_issue("unsafe_backup_name", "backup name must be one plain path component containing a runtime YYYYMMDD timestamp", chosen_name))
        return result
    backup_path = backup_root / chosen_name if final.exists() else None
    result["backup_path"] = str(backup_path) if backup_path is not None else None
    if backup_path is not None and backup_path.exists():
        errors.append(_issue("backup_conflict", "refusing to overwrite an existing backup", backup_path))
        return result
    try:
        candidate_device = candidate.stat().st_dev
        final_device = final.parent.stat().st_dev
        backup_device = backup_root.stat().st_dev
        if candidate_device != final_device:
            errors.append(_issue("candidate_not_atomic", "candidate and final parent are on different filesystems"))
        if final.exists() and final.stat().st_dev != backup_device:
            errors.append(_issue("backup_not_atomic", "final library and backup root are on different filesystems"))
    except OSError as exc:
        errors.append(_issue("promotion_stat_failed", str(exc)))
    if errors:
        return result

    candidate_validation = validate_library(
        candidate, plan, baseline, plan_base=plan_base, baseline_base=baseline_base, require_checksums=True
    )
    result["candidate_validation"] = candidate_validation
    if not candidate_validation["ok"]:
        errors.append(_issue("candidate_validation_failed", "candidate did not pass full-library validation", candidate))
        return result
    special_audit_errors = _special_audit_errors(candidate_validation, state, state_path.parent)
    if special_audit_errors:
        errors.extend(special_audit_errors)
        return result
    if not execute:
        result["ok"] = True
        return result

    backup_moved = False
    candidate_moved = False
    postcheck: Optional[Dict[str, Any]] = None
    state_target = _promotion_state_target(state_path, candidate, final)
    journal_target = _promoted_path(journal_path, candidate, final)
    journal_relative: Optional[Path]
    try:
        journal_relative = journal_path.relative_to(candidate)
    except ValueError:
        journal_relative = None
    try:
        formal_before_manifest = _tree_manifest(final) if final.exists() else {}
        candidate_archive_manifest = _archive_manifest(candidate)
    except (OSError, ValueError) as exc:
        errors.append(_issue("promotion_manifest_failed", str(exc)))
        return result
    journal: Dict[str, Any] = _journal_value(
        {
            "run_id": state.get("run_id"),
            "candidate": str(candidate),
            "formal": str(final),
            "backup": str(backup_path) if backup_path is not None else None,
            "candidate_archive_manifest": candidate_archive_manifest,
            "candidate_summary": candidate_validation.get("summary"),
            "formal_before_manifest": formal_before_manifest,
        },
        "promotion-intent",
    )
    failure_code = "promotion_failed"
    try:
        _write_journal(journal_path, journal)
        if final.exists():
            assert backup_path is not None
            os.rename(str(final), str(backup_path))
            backup_moved = True
            backup_manifest = _tree_manifest(backup_path)
            if backup_manifest != formal_before_manifest:
                raise RuntimeError("backup tree manifest differs from the old formal library")
            journal = _journal_value(journal, "backup-verified", backup_manifest=backup_manifest)
            _write_journal(journal_path, journal)
        if final.exists():
            raise RuntimeError("formal destination unexpectedly exists after backup rename")
        os.rename(str(candidate), str(final))
        candidate_moved = True
        journal = _journal_value(journal, "candidate-promoted", observed_formal_archive_manifest=_archive_manifest(final))
        _write_journal(journal_target, journal)
        postcheck = validate_library(
            final, plan, baseline, plan_base=plan_base, baseline_base=baseline_base, require_checksums=True
        )
        result["postcheck"] = postcheck
        if not postcheck["ok"]:
            failure_code = "PROMOTION_POSTCHECK_FAILED"
            raise RuntimeError("formal-path postcheck failed")
        if postcheck.get("summary") != candidate_validation.get("summary"):
            failure_code = "PROMOTION_POSTCHECK_FAILED"
            raise RuntimeError("formal-path archive count or page totals differ from the validated candidate")
        if _archive_manifest(final) != candidate_archive_manifest:
            failure_code = "PROMOTION_POSTCHECK_FAILED"
            raise RuntimeError("formal-path archive manifest differs from the validated candidate")
        if backup_moved and (backup_path is None or not backup_path.is_dir()):
            failure_code = "PROMOTION_POSTCHECK_FAILED"
            raise RuntimeError("formal-path postcheck could not confirm the backup directory")
        if backup_moved and backup_path is not None and _tree_manifest(backup_path) != formal_before_manifest:
            failure_code = "PROMOTION_POSTCHECK_FAILED"
            raise RuntimeError("formal-path postcheck could not verify backup content")
        updated_state = _deep_merge(_relocate_control_paths(state, candidate, final), {
            "status": "validated-final",
            "current_stage": "validated-final",
            "formal_library": str(final),
            "formal_library_baseline": {
                "root": str(final),
                "archives": _archive_manifest(final),
            },
            "candidate_manifest": {"root": str(final), "archives": _archive_manifest(final)},
            "candidate_library_status": "validated-final",
            "promotion_journal": str(journal_target),
            "promotion": {
                "backup_path": str(backup_path) if backup_path is not None else None,
                "formal_path_postcheck": True,
                "validated_at": datetime.now(timezone.utc).isoformat(),
            },
        })
        updated_state = _prepare_state(updated_state, int(state.get("checkpoint_sequence", 0)))
        state_validation_errors = _resume_state_errors(updated_state)
        state_validation_errors.extend(_state_fingerprint_errors(updated_state, state_target.parent))
        if state_validation_errors:
            raise RuntimeError("final resume state is invalid: {}".format(state_validation_errors))
        _atomic_write_json(state_target, updated_state)
        journal = _journal_value(
            journal,
            "validated-final",
            formal_path_postcheck=True,
            postcheck_summary=postcheck.get("summary"),
            state_path=str(state_target),
            backup_verified=bool(not backup_moved or (backup_path and backup_path.is_dir())),
        )
        _write_journal(journal_target, journal)
        result["state"] = str(state_target)
        result["promotion_journal"] = str(journal_target)
        result["backup_verified"] = bool(not backup_moved or backup_path is not None)
        result["executed"] = True
        result["ok"] = True
        return result
    except Exception as exc:
        errors.append(_issue(failure_code, str(exc)))
        preserved_path: Optional[Path] = None
        rollback_errors: List[str] = []
        active_journal = journal_target if candidate_moved else journal_path
        try:
            journal = _journal_value(journal, "rollback-pending", failure_code=failure_code, failure=str(exc))
            _write_journal(active_journal, journal)
        except Exception as journal_exc:
            rollback_errors.append("cannot persist rollback-pending journal: {}".format(journal_exc))
        if not candidate_moved and candidate.exists():
            preserved_path = candidate
            result["failed_candidate_preserved"] = True
            result["failed_candidate_path"] = str(candidate)
        if candidate_moved and final.exists():
            targets = [candidate]
            if candidate.exists():
                targets = [candidate.parent / "{}.failed-promotion-{}".format(candidate.name, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))]
            for target in targets:
                if target.exists():
                    rollback_errors.append("failed-candidate destination already exists: {}".format(target))
                    continue
                try:
                    os.rename(str(final), str(target))
                    preserved_path = target
                    if journal_relative is not None:
                        active_journal = target / journal_relative
                    result["failed_candidate_preserved"] = True
                    result["failed_candidate_path"] = str(target)
                    break
                except OSError as move_exc:
                    rollback_errors.append("cannot preserve failed candidate: {}".format(move_exc))
        if backup_moved and backup_path is not None:
            if final.exists():
                rollback_errors.append("cannot restore backup because formal path is occupied")
            else:
                try:
                    os.rename(str(backup_path), str(final))
                    if _tree_manifest(final) != formal_before_manifest:
                        rollback_errors.append("restored formal library manifest differs from the pre-promotion manifest")
                    else:
                        result["rolled_back"] = True
                        result["rollback_validation"] = True
                except (OSError, ValueError) as restore_exc:
                    rollback_errors.append("cannot restore backup: {}".format(restore_exc))
        elif not backup_moved and (preserved_path is not None or not final.exists()):
            result["rolled_back"] = True
            result["rollback_validation"] = True
        rollback_status = "rollback-complete" if result["rolled_back"] and not rollback_errors else "rollback-failed"
        try:
            journal = _journal_value(
                journal,
                rollback_status,
                restored_formal_manifest=_tree_manifest(final) if final.is_dir() else None,
                failed_candidate_path=str(preserved_path) if preserved_path is not None else None,
                rollback_errors=rollback_errors,
            )
            _write_journal(active_journal, journal)
            result["promotion_journal"] = str(active_journal)
        except Exception as journal_exc:
            rollback_errors.append("cannot persist final rollback journal: {}".format(journal_exc))
        rollback_state_path = state_path
        if candidate_moved and preserved_path is not None:
            try:
                rollback_state_path = preserved_path / state_path.relative_to(candidate)
            except ValueError:
                rollback_state_path = state_path
        try:
            rollback_base: Mapping[str, Any] = state
            if preserved_path is not None:
                rollback_base = _relocate_control_paths(state, candidate, preserved_path)
            rollback_state = _deep_merge(rollback_base, {
                "status": rollback_status,
                "current_stage": rollback_status,
                "promotion_journal": str(active_journal),
                "promotion": {
                    "failed": True,
                    "failure_code": failure_code,
                    "formal_restored": result["rolled_back"],
                    "failed_candidate_path": str(preserved_path) if preserved_path is not None else None,
                },
            })
            rollback_state = _prepare_state(rollback_state, int(state.get("checkpoint_sequence", 0)))
            _atomic_write_json(rollback_state_path, rollback_state)
            result["state"] = str(rollback_state_path)
        except Exception as state_exc:
            rollback_errors.append("cannot persist rollback state: {}".format(state_exc))
        for message in rollback_errors:
            errors.append(_issue("rollback_failed", message))
        return result


def recover_promotion(journal_path: Path, state_path: Path, execute: bool) -> Dict[str, Any]:
    journal_path = _absolute(journal_path)
    state_path = _absolute(state_path)
    result: Dict[str, Any] = {
        "command": "recover-promotion",
        "journal": str(journal_path),
        "state": str(state_path),
        "dry_run": not execute,
        "executed": False,
        "ok": False,
        "errors": [],
    }
    errors: List[Dict[str, Any]] = result["errors"]
    try:
        journal = _read_json_file(journal_path, "promotion journal")
        state = _read_json_file(state_path, "state")
    except ValueError as exc:
        errors.append(_issue("recovery_input_failed", str(exc)))
        return result
    if not isinstance(journal, Mapping) or not isinstance(state, Mapping):
        errors.append(_issue("recovery_input_invalid", "journal and state must be JSON objects"))
        return result
    candidate_raw = journal.get("candidate")
    formal_raw = journal.get("formal")
    if not isinstance(candidate_raw, str) or not candidate_raw or not isinstance(formal_raw, str) or not formal_raw:
        errors.append(_issue("promotion_journal_paths_invalid", "journal candidate and formal paths must be absolute path strings", journal_path))
        return result
    journal_candidate = _absolute(Path(candidate_raw))
    journal_formal = _absolute(Path(formal_raw))
    errors.extend(_resume_state_errors(state))
    if journal.get("run_id") != state.get("run_id"):
        errors.append(_issue("promotion_journal_run_mismatch", "journal and state belong to different runs", journal_path))
    state_journal_raw = state.get("promotion_journal")
    if isinstance(state_journal_raw, str) and state_journal_raw.strip():
        expected_state_journal = Path(state_journal_raw)
        if not expected_state_journal.is_absolute():
            expected_state_journal = state_path.parent / expected_state_journal
        expected_state_journal = _absolute(expected_state_journal)
    else:
        expected_state_journal = _absolute(state_path.parent / "promotion-journal.json")
    if journal_path != expected_state_journal:
        errors.append(_issue("promotion_journal_state_mismatch", "--journal is not the journal bound to this resume state", journal_path, expected=str(expected_state_journal)))
    state_candidate = _state_path(state, state_path.parent, ("staging_path", "staging", "candidate_library"))
    state_formal = _state_path(state, state_path.parent, ("formal_library", "final_library", "formal_path"))
    if state_candidate != journal_candidate or state_formal != journal_formal:
        errors.append(_issue(
            "promotion_journal_path_mismatch",
            "journal candidate/formal paths differ from resume state",
            journal_path,
            state_candidate=str(state_candidate) if state_candidate else None,
            state_formal=str(state_formal) if state_formal else None,
        ))
    if not journal_candidate.exists() and journal_formal.is_dir():
        state = _relocate_control_paths(state, journal_candidate, journal_formal)
    errors.extend(_state_fingerprint_errors(state, state_path.parent))
    expected_journal_checksum = journal.get("journal_checksum")
    actual_journal_checksum = _state_checksum({key: value for key, value in journal.items() if key != "journal_checksum"})
    if expected_journal_checksum != actual_journal_checksum:
        errors.append(_issue("promotion_journal_checksum_mismatch", "promotion journal checksum is invalid", journal_path))
    source_root = _state_path(state, state_path.parent, ("source_root", "source_path", "source"))
    source_records = _state_hash_records(state, ("source_hashes", "source_manifest", "source_files"), source_root)
    source_errors, source_checked = _verify_manifest(source_records, source_root or state_path.parent, "source")
    errors.extend(source_errors)
    result["source_hashes_checked"] = source_checked
    if errors:
        return result

    inspection = resume_check(state_path)
    recovery = inspection.get("promotion_recovery")
    if not isinstance(recovery, Mapping):
        errors.append(_issue("promotion_recovery_unavailable", "journal does not describe a recoverable in-progress promotion", journal_path))
        return result
    action = recovery.get("action")
    observations = recovery.get("observations", {})
    result["action"] = action
    result["observations"] = observations
    safe_actions = {
        "close-noop-intent-and-restart-promotion",
        "continue-candidate-promotion-then-postcheck",
        "run-formal-postcheck-and-finalize",
        "mark-rollback-complete",
    }
    if action not in safe_actions:
        errors.append(_issue("promotion_recovery_ambiguous", "current directory manifests do not permit automatic recovery", journal_path, observations=observations))
        return result
    if not execute:
        result["ok"] = True
        return result

    candidate = _absolute(Path(str(journal.get("candidate"))))
    formal = _absolute(Path(str(journal.get("formal"))))
    backup_raw = journal.get("backup")
    backup = _absolute(Path(backup_raw)) if isinstance(backup_raw, str) and backup_raw else None
    expected_candidate = dict(journal.get("candidate_archive_manifest", {}))
    expected_formal = dict(journal.get("formal_before_manifest", {}))
    expected_backup = dict(journal.get("backup_manifest", expected_formal))
    state_target = _promotion_state_target(state_path, candidate, formal)
    journal_target = _promoted_path(journal_path, candidate, formal)

    if action in {"close-noop-intent-and-restart-promotion", "mark-rollback-complete"}:
        terminal = _journal_value(journal, "rollback-complete", recovery_action=action)
        archived_journal = journal_path.with_name(
            "promotion-journal.recovered-{}.json".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        )
        _write_journal(journal_path, terminal)
        os.replace(str(journal_path), str(archived_journal))
        updated = copy.deepcopy(dict(state))
        updated.pop("promotion_journal", None)
        updated = _deep_merge(updated, {"status": "rollback-complete", "current_stage": "rollback-complete"})
        updated = _prepare_state(updated, int(state.get("checkpoint_sequence", 0)))
        _atomic_write_json(state_path, updated)
        result.update({"ok": True, "executed": True, "archived_journal": str(archived_journal), "state": str(state_path)})
        return result

    plan_path = Path(str(state.get("plan_path")))
    if not plan_path.is_absolute():
        plan_path = state_path.parent / plan_path
    try:
        plan = _read_json_file(plan_path, "plan")
    except ValueError as exc:
        errors.append(_issue("recovery_plan_failed", str(exc), plan_path))
        return result

    candidate_was_moved = False
    active_journal = journal_path
    failed_candidate: Optional[Path] = None
    try:
        if action == "continue-candidate-promotion-then-postcheck":
            backup_ready = (backup is None and not expected_backup) or (
                backup is not None and backup.is_dir() and _tree_manifest(backup) == expected_backup
            )
            if formal.exists() or not candidate.is_dir() or not backup_ready:
                raise RuntimeError("recovery preconditions changed before execution")
            os.rename(str(candidate), str(formal))
            candidate_was_moved = True
            active_journal = journal_target
            journal = _journal_value(journal, "candidate-promoted-recovery")
            _write_journal(active_journal, journal)
        if _archive_manifest(formal) != expected_candidate:
            raise RuntimeError("formal archive manifest does not match the approved candidate")
        postcheck = validate_library(formal, plan=plan, plan_base=plan_path.parent, require_checksums=True)
        result["postcheck"] = postcheck
        if not postcheck["ok"]:
            raise RuntimeError("formal-path postcheck failed during recovery")
        special_errors = _special_audit_errors(postcheck, state, state_path.parent)
        if special_errors:
            raise RuntimeError("Special audit failed during recovery: {}".format(special_errors))
        if backup is not None and (_tree_manifest(backup) != expected_backup):
            raise RuntimeError("backup manifest changed during recovery")
        updated = _deep_merge(_relocate_control_paths(state, candidate, formal), {
            "status": "validated-final",
            "current_stage": "validated-final",
            "formal_library": str(formal),
            "formal_library_baseline": {"root": str(formal), "archives": _archive_manifest(formal)},
            "candidate_manifest": {"root": str(formal), "archives": _archive_manifest(formal)},
            "candidate_library_status": "validated-final",
            "promotion_journal": str(active_journal),
            "promotion": {"recovered": True, "backup_path": str(backup) if backup else None, "formal_path_postcheck": True},
        })
        updated = _prepare_state(updated, int(state.get("checkpoint_sequence", 0)))
        updated_errors = _resume_state_errors(updated) + _state_fingerprint_errors(updated, state_target.parent)
        if updated_errors:
            raise RuntimeError("recovered final state is invalid: {}".format(updated_errors))
        _atomic_write_json(state_target, updated)
        journal = _journal_value(journal, "validated-final", recovered=True, state_path=str(state_target))
        _write_journal(active_journal, journal)
        result.update({"ok": True, "executed": True, "state": str(state_target), "journal": str(active_journal)})
        return result
    except Exception as exc:
        errors.append(_issue("PROMOTION_POSTCHECK_FAILED", str(exc)))
        try:
            if formal.is_dir() and (_archive_manifest(formal) == expected_candidate or candidate_was_moved):
                failed_candidate = candidate
                if failed_candidate.exists():
                    failed_candidate = candidate.parent / "{}.failed-recovery-{}".format(
                        candidate.name, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    )
                os.rename(str(formal), str(failed_candidate))
                active_journal = _promoted_path(active_journal, formal, failed_candidate)
            if backup is not None and backup.is_dir() and not formal.exists():
                os.rename(str(backup), str(formal))
            if expected_formal and _tree_manifest(formal) != expected_formal:
                raise RuntimeError("restored formal library differs from pre-promotion manifest")
            journal = _journal_value(
                journal,
                "rollback-complete",
                recovery_failed=True,
                failed_candidate_path=str(failed_candidate) if failed_candidate else None,
            )
            _write_journal(active_journal, journal)
            result["rolled_back"] = True
            result["failed_candidate_path"] = str(failed_candidate) if failed_candidate else None
            recovery_state_path = state_path
            recovery_state: Mapping[str, Any] = state
            if failed_candidate is not None:
                if _path_within(state_path, formal):
                    recovery_state_path = failed_candidate / state_path.relative_to(formal)
                    recovery_state = _relocate_control_paths(state, formal, failed_candidate)
                elif _path_within(state_path, candidate):
                    recovery_state_path = failed_candidate / state_path.relative_to(candidate)
                    recovery_state = _relocate_control_paths(state, candidate, failed_candidate)
            recovery_state = _deep_merge(recovery_state, {
                "status": "rollback-complete",
                "current_stage": "rollback-complete",
                "candidate_library_status": "failed-candidate-preserved",
                "candidate_manifest": {
                    "root": str(failed_candidate) if failed_candidate else str(candidate),
                    "archives": _archive_manifest(failed_candidate) if failed_candidate and failed_candidate.is_dir() else {},
                },
                "promotion_journal": str(active_journal),
                "promotion": {
                    "recovered": False,
                    "formal_path_postcheck": False,
                    "rollback_complete": True,
                    "failed_candidate_path": str(failed_candidate) if failed_candidate else None,
                },
            })
            recovery_state = _prepare_state(recovery_state, int(state.get("checkpoint_sequence", 0)))
            _atomic_write_json(recovery_state_path, recovery_state)
            result["state"] = str(recovery_state_path)
        except Exception as rollback_exc:
            errors.append(_issue("rollback_failed", str(rollback_exc)))
        return result


def _coalesce_path(positional: Optional[str], option: Optional[str], label: str) -> Path:
    if positional and option:
        raise CliUsageError("provide {} either positionally or by option, not both".format(label))
    raw = option or positional
    if not raw:
        raise CliUsageError("{} is required".format(label))
    return Path(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a complete candidate or formal library")
    validate.add_argument("library_pos", nargs="?", help="library directory")
    validate.add_argument("--library", dest="library_opt", help="library directory")
    validate.add_argument("--plan", help="optional plan/chapter-boundaries JSON")
    validate.add_argument("--baseline", help="optional baseline/unaffected-archive JSON")
    validate.add_argument("--require-checksums", action="store_true", help="require and verify _reports/checksums.sha256")

    checkpoint = subparsers.add_parser("checkpoint", help="atomically create or explicitly update resume state")
    checkpoint.add_argument("state_pos", nargs="?", help="resume-state JSON path")
    checkpoint.add_argument("--state", dest="state_opt", help="resume-state JSON path")
    checkpoint.add_argument("--state-json", "--json", dest="state_json", help="full/merge state JSON, inline or file")
    checkpoint.add_argument("--update-json", help="merge patch JSON, inline or file")
    checkpoint.add_argument("--update", action="store_true", help="explicitly permit updating an existing state")
    checkpoint_mode = checkpoint.add_mutually_exclusive_group()
    checkpoint_mode.add_argument("--execute", action="store_true", help="perform the atomic state write")
    checkpoint_mode.add_argument("--dry-run", action="store_true", help="show the proposed state without writing (default)")

    resume = subparsers.add_parser("resume-check", help="verify checkpoint hashes and choose deterministic resume actions")
    resume.add_argument("state_pos", nargs="?", help="resume-state JSON path")
    resume.add_argument("--state", dest="state_opt", help="resume-state JSON path")
    resume.add_argument("--source", help="explicit source root override")
    resume.add_argument("--formal-library", help="explicit formal library root override")
    resume.add_argument("--staging", help="explicit staging directory override")

    promote = subparsers.add_parser("promote", help="validate and atomically promote a candidate with rollback")
    promote.add_argument("--candidate", required=True, help="validated candidate library directory")
    promote.add_argument("--final", required=True, help="formal library path")
    promote.add_argument("--backup-root", required=True, help="existing external backup directory")
    promote.add_argument("--state", required=True, help="resume-state JSON path")
    promote.add_argument("--plan", help="optional plan/chapter-boundaries JSON")
    promote.add_argument("--baseline", help="optional baseline/unaffected-archive JSON")
    promote.add_argument("--backup-name", help="explicit non-conflicting backup directory name")
    promote_mode = promote.add_mutually_exclusive_group()
    promote_mode.add_argument("--execute", action="store_true", help="perform promotion and state update")
    promote_mode.add_argument("--dry-run", action="store_true", help="validate and show the promotion without renaming (default)")

    recover = subparsers.add_parser("recover-promotion", help="reconcile and resume or roll back an interrupted journaled promotion")
    recover.add_argument("--journal", required=True, help="in-progress promotion-journal JSON path")
    recover.add_argument("--state", required=True, help="matching resume-state JSON path")
    recover_mode = recover.add_mutually_exclusive_group()
    recover_mode.add_argument("--execute", action="store_true", help="perform only the manifest-proven recovery action")
    recover_mode.add_argument("--dry-run", action="store_true", help="show the deterministic recovery action without mutation (default)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "validate":
            library = _coalesce_path(args.library_pos, args.library_opt, "library")
            plan_path = Path(args.plan) if args.plan else None
            baseline_path = Path(args.baseline) if args.baseline else None
            plan = _read_json_file(plan_path, "plan") if plan_path else None
            baseline = _read_json_file(baseline_path, "baseline") if baseline_path else None
            result = validate_library(
                library, plan, baseline,
                plan_base=plan_path.parent.resolve() if plan_path else Path.cwd(),
                baseline_base=baseline_path.parent.resolve() if baseline_path else Path.cwd(),
                require_checksums=args.require_checksums,
            )
        elif args.command == "checkpoint":
            state_path = _coalesce_path(args.state_pos, args.state_opt, "state")
            state_json = _read_json_value(args.state_json, "state") if args.state_json else None
            update_json = _read_json_value(args.update_json, "update") if args.update_json else None
            result = checkpoint_state(state_path, state_json, update_json, args.update, args.execute)
        elif args.command == "resume-check":
            state_path = _coalesce_path(args.state_pos, args.state_opt, "state")
            result = resume_check(
                state_path,
                source_override=Path(args.source) if args.source else None,
                formal_override=Path(args.formal_library) if args.formal_library else None,
                staging_override=Path(args.staging) if args.staging else None,
            )
        elif args.command == "promote":
            plan_path = Path(args.plan) if args.plan else None
            baseline_path = Path(args.baseline) if args.baseline else None
            plan = _read_json_file(plan_path, "plan") if plan_path else None
            baseline = _read_json_file(baseline_path, "baseline") if baseline_path else None
            result = promote_library(
                Path(args.candidate), Path(args.final), Path(args.backup_root), Path(args.state),
                args.execute, plan, baseline,
                plan_base=plan_path.parent.resolve() if plan_path else Path.cwd(),
                baseline_base=baseline_path.parent.resolve() if baseline_path else Path.cwd(),
                backup_name=args.backup_name,
                plan_file=plan_path,
            )
        elif args.command == "recover-promotion":
            result = recover_promotion(Path(args.journal), Path(args.state), args.execute)
        else:
            raise CliUsageError("unknown command")
        _json_print(result)
        return 0 if result.get("ok") else 1
    except CleanParserExit as exc:
        return exc.status
    except CliUsageError as exc:
        _json_print({"command": None, "ok": False, "errors": [_issue("usage_error", str(exc))]})
        return 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        command = None
        if argv:
            command = argv[0]
        elif len(sys.argv) > 1:
            command = sys.argv[1]
        _json_print({"command": command, "ok": False, "errors": [_issue("input_error", str(exc))]})
        return 2
    except Exception as exc:
        command = argv[0] if argv else (sys.argv[1] if len(sys.argv) > 1 else None)
        _json_print({"command": command, "ok": False, "errors": [_issue("internal_error", str(exc))]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
