#!/usr/bin/env python3
"""Run Manga Organizer's destructive-workflow tests on a synthetic CBZ library.

The harness creates only generated images and archives below ``--workdir`` (or
an automatically deleted temporary directory).  It never discovers or reads a
real manga library.  The final stdout value is one machine-readable JSON object.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - checked in main before creating artifacts.
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]


SERIES = "合成系列"


class TestFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_bytes(seed: int, variation: int = 0) -> bytes:
    image = Image.new("RGB", (96, 128), (245, 245, 242))
    draw = ImageDraw.Draw(image)
    color = ((seed * 43) % 220, (seed * 71) % 220, (seed * 97) % 220)
    draw.rectangle((8, 8, 87, 119), outline=color, width=4)
    draw.line((10, 20 + seed % 20, 84, 100 - seed % 15), fill=color, width=5)
    draw.ellipse((22 + seed % 9, 35, 66, 79), fill=color)
    if variation:
        draw.rectangle((88 - variation, 120 - variation, 90, 122), fill=(10, 10, 10))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def comicinfo(
    *,
    number: str | None = None,
    volume: str | None = None,
    fmt: str | None = None,
    page_count: int,
    title: str | None = None,
) -> bytes:
    root = ET.Element("ComicInfo")
    values = {
        "Series": SERIES,
        "LocalizedSeries": SERIES,
        "SeriesSort": SERIES,
        "Title": title or (f"第{number}话" if number else f"第{volume}卷"),
        "Number": number,
        "Volume": volume,
        "Format": fmt,
        "PageCount": str(page_count),
        "LanguageISO": "zh",
        "Manga": "YesAndRightToLeft",
    }
    for key, value in values.items():
        if value is not None:
            ET.SubElement(root, key).text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_cbz(
    path: Path,
    pages: Iterable[bytes],
    *,
    number: str | None = None,
    volume: str | None = None,
    fmt: str | None = None,
    title: str | None = None,
) -> Path:
    page_list = list(pages)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, data in enumerate(page_list, 1):
            archive.writestr(f"{index:04}.png", data)
        archive.writestr(
            "ComicInfo.xml",
            comicinfo(number=number, volume=volume, fmt=fmt, page_count=len(page_list), title=title),
        )
    return path


def identity(path: Path) -> dict[str, str | None]:
    with zipfile.ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("ComicInfo.xml"))
    result: dict[str, str | None] = {}
    for field in ("Series", "LocalizedSeries", "SeriesSort", "Title", "Number", "Volume", "Format", "PageCount", "Notes"):
        node = root.find(field)
        result[field] = node.text if node is not None else None
    return result


def members(path: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(path, "r") as archive:
        return [(info.filename, hashlib.sha256(archive.read(info)).hexdigest()) for info in archive.infolist()]


def image_hashes(path: Path) -> list[str]:
    return [digest for name, digest in members(path) if name != "ComicInfo.xml"]


def image_evidence(path: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    with zipfile.ZipFile(path, "r") as archive:
        infos = [item for item in archive.infolist() if Path(item.filename).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}]
        for info in infos:
            data = archive.read(info)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                dimensions = [image.width, image.height]
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                rgb = image.convert("RGB").resize((8, 8), resampling)
                rgb_pixels = list(rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata())
                grayscale = image.convert("L").resize((9, 8), resampling)
                pixels = list(grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata())
            difference_bits = 0
            for row in range(8):
                offset = row * 9
                for column in range(8):
                    difference_bits = (difference_bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
            averages = tuple(sum(pixel[channel] for pixel in rgb_pixels) // len(rgb_pixels) for channel in range(3))
            color_bits = (averages[0] << 16) | (averages[1] << 8) | averages[2]
            evidence.append({
                "sha256": hashlib.sha256(data).hexdigest(),
                "perceptual_hash": f"{((difference_bits << 24) | color_bits):022x}",
                "dimensions": dimensions,
            })
    return evidence


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_checksums(library: Path) -> Path:
    rows = []
    for archive in sorted(library.rglob("*.cbz"), key=lambda item: item.relative_to(library).as_posix()):
        rows.append(f"{sha256(archive)}  {archive.relative_to(library).as_posix()}")
    path = library / "_reports" / "checksums.sha256"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def synthetic_digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def promotion_state_payload(
    *,
    label: str,
    source_root: Path,
    source_file: Path,
    final: Path,
    backups: Path,
    candidate: Path,
    formal_archives: dict[str, str],
    candidate_archives: dict[str, str],
    tool_root: Path,
    controls_root: Path | None = None,
) -> dict[str, Any]:
    controls = controls_root or (source_root.parent / "controls")
    config_path = write_json(controls / "config.json", {"profile": "kavita-chapter", "identity_policy": "continuous-chapter"})
    plan_path = write_json(controls / "plan.json", {"identity_policy": "continuous-chapter"})
    decision_path = write_json(controls / "decision-resolution.json", {"decisions": []})
    return {
        "schema_version": 1,
        "run_id": f"synthetic-{label}-run",
        "status": "candidate-ready",
        "current_stage": "candidate-ready",
        "source_root": str(source_root),
        "formal_library": str(final),
        "backup_directory": str(backups),
        "staging_path": str(candidate),
        "last_complete_unit": "candidate-validation",
        "completed_archives": sorted(candidate_archives),
        "pending_archives": [],
        "source_hashes": {str(source_file): sha256(source_file)},
        "formal_library_baseline": {"root": str(final), "archives": formal_archives},
        "chapter_boundaries": {},
        "ocr_permissions": {"authorized": False, "scope": []},
        "ocr_review_conclusions": {},
        "primary_editions": {},
        "ignored_damaged_items": [],
        "special_deduplication": {},
        "locked_metadata": {"Series": SERIES},
        "candidate_library_status": "validated-candidate",
        "candidate_manifest": {"root": str(candidate), "archives": candidate_archives},
        "profile": "kavita-chapter",
        "identity_policy": "continuous-chapter",
        "tool_fingerprint": {
            "library_state.py": sha256(tool_root / "scripts" / "library_state.py"),
            "cbz_transform.py": sha256(tool_root / "scripts" / "cbz_transform.py"),
        },
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "plan_path": str(plan_path),
        "plan_sha256": sha256(plan_path),
        "decision_log_path": str(decision_path),
        "decision_log_sha256": sha256(decision_path),
        "lock_owner": "synthetic-test",
        "filesystem_capabilities": {"atomic_rename": True},
        "review_copy_hashes": {},
        "unaffected_archive_hashes": dict(formal_archives),
        "affected_formal_archives": [],
    }


class Runner:
    def __init__(self, skill_root: Path) -> None:
        self.transform = skill_root / "scripts" / "cbz_transform.py"
        self.state = skill_root / "scripts" / "library_state.py"

    def run(self, tool: Path, args: list[str], expected: int = 0) -> dict[str, Any]:
        process = subprocess.run(
            [sys.executable, str(tool), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise TestFailure(
                f"{tool.name} did not emit JSON (exit={process.returncode}): {process.stdout!r} {process.stderr!r}"
            ) from exc
        require(process.returncode == expected, f"{tool.name} exit {process.returncode}, expected {expected}: {payload}")
        return payload


def validate(runner: Runner, library: Path, plan: Path | None = None) -> dict[str, Any]:
    args = ["validate", "--library", str(library)]
    if plan is not None:
        args += ["--plan", str(plan)]
    result = runner.run(runner.state, args)
    require(result["ok"], f"library validation failed: {result['errors']}")
    return result


def normalize(
    runner: Runner,
    source: Path,
    output: Path,
    patch_path: Path,
) -> dict[str, Any]:
    return runner.run(
        runner.transform,
        ["normalize", "--source", str(source), "--output", str(output), "--patch", str(patch_path)],
    )


def case_continuous_identity(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "01-continuous-identity"
    sources = case / "sources"
    output = case / "candidate" / SERIES
    specs = [(42, 8), (47, 9), (62, None)]
    audit_rows = []
    for chapter, volume in specs:
        source_name = f"{SERIES} Vol.{volume:02} Ch.{chapter:03}.cbz" if volume else f"{SERIES} Ch.{chapter:03}.cbz"
        source = write_cbz(sources / source_name, [image_bytes(chapter)], number=str(chapter), volume=str(volume) if volume else None)
        target = output / f"{SERIES} Ch.{chapter:03}.cbz"
        if volume:
            patch = write_json(
                case / f"patch-{chapter}.json",
                {"identity": {"Number": str(chapter), "Volume": None}, "change_reason": "continuous chapter identity"},
            )
            row = normalize(runner, source, target, patch)
            require(row["ok"] and row["non_xml_bytes_preserved"], "normalization did not preserve members")
            require(row["source_volume_provenance"] == f"Source volume: {volume}", "source volume provenance was not retained")
            audit_rows.append(row)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        current = identity(target)
        require(current["Number"] == str(chapter) and current["Volume"] is None, "continuous identity still carries Volume")
        require("Vol." not in target.name, "continuous filename still carries Vol")
    plan = write_json(
        case / "plan.json",
        {
            "identity_policy": "continuous-chapter",
            "series": SERIES,
            "deliberate_missing_ranges": [
                {"start": 43, "end": 46, "reason": "synthetic fixture omits unrelated identities", "confirmed": True},
                {"start": 48, "end": 61, "reason": "synthetic fixture omits unrelated identities", "confirmed": True},
            ],
        },
    )
    report = validate(runner, case / "candidate", plan)
    require(report["series_identity_audit"][0]["normal_chapter_count"] == 3, "chapter audit count differs")

    split_source = write_cbz(
        case / "split-source.cbz",
        [image_bytes(901), image_bytes(902), image_bytes(903), image_bytes(904)],
        volume="10",
    )
    split_output = case / "split-candidate" / SERIES
    split_plan = write_json(
        case / "split-boundaries.json",
        {
            "source": str(split_source),
            "source_page_count": 4,
            "identity_policy": "continuous-chapter",
            "visual_review_complete": True,
            "boundaries": [
                {"kind": "chapter", "start": 1, "end": 2, "output": str(split_output / f"{SERIES} Ch.001.cbz"), "number": "1"},
                {"kind": "chapter", "start": 3, "end": 4, "output": str(split_output / f"{SERIES} Ch.002.cbz"), "number": "2"},
            ],
        },
    )
    split_result = runner.run(
        runner.transform,
        ["split", "--source", str(split_source), "--boundaries", str(split_plan)],
    )
    require(split_result["ok"] and split_result["source_unchanged"], "valid CBZ split failed")
    require(
        split_result["coverage"]["continuous"]
        and split_result["coverage"]["non_overlapping"]
        and split_result["coverage"]["exact"],
        "split did not document exact coverage",
    )
    for output_row in split_result["outputs"]:
        require(all(page["byte_identical"] for page in output_row["page_mapping"]), "split re-encoded an image")
        current = identity(Path(output_row["output"]))
        require(current["Volume"] is None and current["Notes"] == "Source volume: 10", "split identity/provenance differs")
    validate(runner, case / "split-candidate", write_json(case / "split-plan.json", {"identity_policy": "continuous-chapter"}))
    return {"normalized": len(audit_rows), "chapters": [42, 47, 62], "split_archives": 2, "split_image_bytes_preserved": True}


def fallback_plan(
    output: str,
    volume: int,
    pages: int,
    source: Path,
    compared: list[dict[str, Any]] | None = None,
    fallback_archive: Path | None = None,
) -> dict[str, Any]:
    compared = sorted(compared or [], key=lambda item: item["path"])
    current_evidence = image_evidence(fallback_archive) if fallback_archive is not None else []
    perceptual_candidates: list[dict[str, Any]] = []
    for item in compared:
        for fallback_page, current in enumerate(current_evidence, 1):
            for compared_page, target in enumerate(item.get("page_evidence", []), 1):
                distance = (int(current["perceptual_hash"], 16) ^ int(target["perceptual_hash"], 16)).bit_count()
                if distance <= 4:
                    perceptual_candidates.append({
                        "fallback_page": fallback_page,
                        "fallback_page_sha256": current["sha256"],
                        "compared_path": item["path"],
                        "compared_page": compared_page,
                        "compared_page_sha256": target["sha256"],
                        "hamming_distance": distance,
                    })
    perceptual_candidates.sort(key=lambda item: (item["compared_path"], item["fallback_page"], item["compared_page"]))
    return {
        "source": str(source),
        "source_sha256": sha256(source),
        "boundary_method": "allowed-evidence-exhausted",
        "series": SERIES,
        "output": output,
        "packaging_mode": "volume-fallback",
        "volume": volume,
        "chapter": None,
        "series_confirmed": True,
        "volume_confirmed": True,
        "single_complete_volume": True,
        "all_pages_readable": True,
        "natural_order_confirmed": True,
        "source_page_count": pages,
        "coverage": [{"start": 1, "end": pages}],
        "covered_exactly_once": True,
        "attempted_evidence": ["embedded navigation", "visible title pages", "local naming"],
        "fallback_reason": "allowed evidence was exhausted without reliable chapter boundaries",
        "ocr_used": False,
        "cross_package_overlap_checked": True,
        "content_overlap_detected": False,
        "fallback_id": f"synthetic-fallback-v{volume}",
        "overlap_audit": {
            "compared_identities": [
                {"path": item["path"], "archive_sha256": item["archive_sha256"], "canonical_identity": item["canonical_identity"]}
                for item in compared
            ],
            "page_sha256_checked": True,
            "perceptual_candidates_checked": True,
            "visual_review_complete": True,
            "result": "resolved" if perceptual_candidates else "no-overlap",
            "audit_record_id": f"synthetic-overlap-v{volume}",
            "page_sha256_comparisons": [
                {"path": item["path"], "shared_sha256": item.get("shared_sha256", [])} for item in compared
            ],
            "perceptual_hash_algorithm": "dhash-88-color",
            "perceptual_distance": 4,
            "perceptual_candidates": perceptual_candidates,
            "visual_decisions": [
                {**item, "duplicate": False, "reviewer": "synthetic-test", "reason": "visually distinct synthetic pages"}
                for item in perceptual_candidates
            ],
        },
    }


def case_volume_fallback(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "02-volume-fallback"
    library = case / "candidate"
    output = f"{SERIES}/{SERIES} v05.cbz"
    fallback_archive = write_cbz(library / output, [image_bytes(501), image_bytes(502), image_bytes(503)], volume="5")
    source = case / "sources" / "volume-source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic-complete-volume-source")
    record = fallback_plan(output, 5, 3, source, fallback_archive=fallback_archive)
    plan = write_json(case / "plan.json", {"identity_policy": "continuous-chapter", "items": [record]})
    report = validate(runner, library, plan)
    current = identity(library / output)
    require(current["Volume"] == "5" and current["Number"] is None, "fallback identity is not volume-only")
    require(report["summary"]["fallback_count"] == 1, "fallback was not counted")
    require(report["series_identity_audit"][0]["volume_fallback_count"] == 1, "series fallback audit differs")
    invalid_record = dict(record)
    invalid_record["attempted_evidence"] = []
    invalid_plan = write_json(case / "invalid-plan.json", {"identity_policy": "continuous-chapter", "items": [invalid_record]})
    invalid = runner.run(runner.state, ["validate", "--library", str(library), "--plan", str(invalid_plan)], expected=1)
    require(any(item["code"] == "invalid_volume_fallback" for item in invalid["errors"]), "fallback with missing evidence was accepted")
    return {"output": output, "fallback_reason": record["fallback_reason"], "reviewed": False, "missing_evidence_rejected": True}


def case_mixed_packaging(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "03-mixed-packaging"
    library = case / "candidate"
    series_dir = library / SERIES
    chapter_one = write_cbz(series_dir / f"{SERIES} Ch.001.cbz", [image_bytes(1)], number="1")
    chapter_two = write_cbz(series_dir / f"{SERIES} Ch.002.cbz", [image_bytes(2)], number="2")
    fallback_rel = f"{SERIES}/{SERIES} v04.cbz"
    fallback_archive = write_cbz(library / fallback_rel, [image_bytes(1, 1), image_bytes(402)], volume="4")
    source = case / "sources" / "volume-source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic-mixed-volume-source")
    compared = [
        {
            "path": archive.relative_to(library).as_posix(),
            "archive_sha256": sha256(archive),
            "canonical_identity": f"chapter:{index}",
            "shared_sha256": sorted(set(image_hashes(archive)) & set(image_hashes(library / fallback_rel))),
            "page_evidence": image_evidence(archive),
        }
        for index, archive in ((1, chapter_one), (2, chapter_two))
    ]
    fallback_record = fallback_plan(fallback_rel, 4, 2, source, compared, fallback_archive)
    require(fallback_record["overlap_audit"]["perceptual_candidates"], "synthetic fallback did not create a real perceptual candidate")
    plan = write_json(
        case / "plan.json",
        {"identity_policy": "continuous-chapter", "items": [fallback_record]},
    )
    report = validate(runner, library, plan)
    codes = {item["code"] for item in report["errors"]}
    require("SERIES_IDENTITY_MIX" not in codes, "documented fallback was misreported as identity mix")
    audit = report["series_identity_audit"][0]
    require(audit["normal_chapter_count"] == 2 and audit["volume_fallback_count"] == 1, "mixed audit count differs")
    tampered_record = json.loads(json.dumps(fallback_record))
    tampered_record["overlap_audit"]["perceptual_candidates"] = []
    tampered_record["overlap_audit"]["visual_decisions"] = []
    tampered_plan = write_json(
        case / "tampered-overlap-plan.json",
        {"identity_policy": "continuous-chapter", "items": [tampered_record]},
    )
    rejected = runner.run(runner.state, ["validate", "--library", str(library), "--plan", str(tampered_plan)], expected=1)
    require(any(item["code"] == "invalid_volume_fallback" for item in rejected["errors"]), "fabricated perceptual overlap audit was accepted")
    return {"chapters_without_volume": 2, "documented_fallbacks": 1}


def case_intentional_gap(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "04-intentional-gap"
    library = case / "candidate"
    series_dir = library / SERIES
    for chapter in (1, 2, 5):
        write_cbz(series_dir / f"{SERIES} Ch.{chapter:03}.cbz", [image_bytes(chapter)], number=str(chapter))
    plan = write_json(
        case / "plan.json",
        {
            "identity_policy": "continuous-chapter",
            "series": SERIES,
            "deliberate_missing_ranges": [
                {"start": 3, "end": 4, "reason": "corresponding source volume is absent", "confirmed": True}
            ],
        },
    )
    report = validate(runner, library, plan)
    audit = report["series_identity_audit"][0]
    require(audit["confirmed_source_gaps"] == [3, 4] and not audit["unintended_gaps"], "real gap classification differs")
    require(len(list(series_dir.glob("*.cbz"))) == 3, "gap test generated a placeholder")
    return {"deliberate_missing_ranges": [3, 4], "placeholders": 0}


def case_ten_archive_normalization(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "05-member-integrity"
    reports = []
    for index in range(1, 11):
        source = write_cbz(
            case / "sources" / f"{SERIES} Vol.{index:02} Ch.{index:03}.cbz",
            [image_bytes(index), image_bytes(index + 100)],
            number=str(index),
            volume=str(index),
        )
        before = members(source)
        patch = write_json(
            case / f"patch-{index}.json",
            {"identity": {"Number": str(index), "Volume": None}, "change_reason": "remove partial volume identity"},
        )
        target = case / "candidate" / SERIES / f"{SERIES} Ch.{index:03}.cbz"
        result = normalize(runner, source, target, patch)
        after = members(target)
        require([name for name, _ in before] == [name for name, _ in after], "member order changed")
        for old, new in zip(before, after):
            if old[0] != "ComicInfo.xml":
                require(old == new, "non-XML member changed")
        require(result["identity_before"]["Volume"] == str(index), "identity_before is incomplete")
        require(result["identity_after"]["Volume"] is None, "identity_after still carries Volume")
        require(all(row["member_order_unchanged"] for row in result["member_integrity"]), "audit says order changed")
        reports.append(result)
    validate(runner, case / "candidate", write_json(case / "plan.json", {"identity_policy": "continuous-chapter"}))
    return {"normalized_archives": len(reports), "nonmetadata_byte_mismatches": 0, "member_order_changes": 0}


def case_special_merge(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "06-special-dedup"
    first = image_bytes(700)
    near = image_bytes(700, 1)
    unique = image_bytes(701)
    source_a = write_cbz(case / "sources" / "official-a.cbz", [first, image_bytes(702)], number="SP01", fmt="Special", title="特别篇")
    source_b = write_cbz(case / "sources" / "official-b.cbz", [first, near, unique], number="SP02", fmt="Special", title="特别篇")
    output = case / "candidate" / SERIES / f"{SERIES} SP03.cbz"
    manifest_path = case / "manifest.json"
    manifest: dict[str, Any] = {
        "output": str(output),
        "number": "SP03",
        "identity": {"Series": SERIES, "LocalizedSeries": SERIES, "SeriesSort": SERIES, "Title": "官方特别篇"},
        "perceptual_distance": 16,
        "sources": [
            {"path": str(source_a), "official": True, "confidence": "high"},
            {"path": str(source_b), "official": True, "confidence": "high"},
        ],
    }
    write_json(manifest_path, manifest)
    preview = runner.run(runner.transform, ["merge-specials", "--manifest", str(manifest_path), "--dry-run"])
    unresolved = preview["deduplication"]["unresolved_visual_reviews"]
    visual_review = {
        item["visual_review_key"]: {
            "duplicate": item["source_page"] == 2,
            "reason": "synthetic visual comparison confirmed the decision",
            "reviewer": "synthetic-test",
            "source_archive_sha256": item["source_archive_sha256"],
            "source_page_sha256": item["byte_sha256"],
            "duplicate_target_sha256": item["dedupe_target"]["byte_sha256"],
        }
        for item in unresolved
    }
    require(visual_review, "perceptual candidates were not surfaced for visual review")
    stale_review = json.loads(json.dumps(visual_review))
    first_key = sorted(stale_review)[0]
    stale_review[first_key]["source_page_sha256"] = "0" * 64
    manifest["visual_review"] = stale_review
    write_json(manifest_path, manifest)
    stale = runner.run(runner.transform, ["merge-specials", "--manifest", str(manifest_path)], expected=2)
    require(stale["error"]["code"] == "SPECIAL_VISUAL_REVIEW_STALE", "stale visual decision was accepted")
    manifest["visual_review"] = visual_review
    write_json(manifest_path, manifest)
    result = runner.run(runner.transform, ["merge-specials", "--manifest", str(manifest_path)])
    require(result["ok"] and result["written"], "Special merge did not write output")
    current = identity(output)
    require(current["Number"] == "SP03" and current["Format"] == "Special", "merged Special identity differs")
    require(result["deduplication"]["omitted_count"] >= 2, "duplicate pages were not omitted")
    require(all(path.exists() for path in (source_a, source_b)), "Special sources were not preserved")
    special_plan = {
        "identity_policy": "continuous-chapter",
        "items": [{
            "kind": "merged-special",
            "output": output.relative_to(case / "candidate").as_posix(),
            "series": SERIES,
            "number": "SP03",
            "complete_independent_range": True,
            "identity_confidence": "high",
            "evidence": ["official source identity", "visual review"],
            "page_count": result["output_page_count"],
            "deduplication_status": "complete",
            "audit_record_id": "synthetic-special-audit-SP03",
            "source_components": [
                {
                    "source": source_row["path"],
                    "source_sha256": source_row["archive_sha256"],
                    "included_source_pages": [
                        page["source_page"] for page in result["kept_pages"] if page["source"] == source_row["path"]
                    ],
                }
                for source_row in result["sources"]
            ],
            "output_page_mapping": [
                {
                    "output_page": page["output_page"],
                    "source": page["source"],
                    "source_page": page["source_page"],
                    "source_archive_sha256": next(
                        source_row["archive_sha256"] for source_row in result["sources"] if source_row["path"] == page["source"]
                    ),
                    "source_page_sha256": page["byte_sha256"],
                    "output_sha256": page["byte_sha256"],
                }
                for page in result["kept_pages"]
            ],
        }],
    }
    forged_plan = json.loads(json.dumps(special_plan))
    forged_mapping = forged_plan["items"][0]["output_page_mapping"][0]
    source_page_count = next(
        source_row["page_count"] for source_row in result["sources"] if source_row["path"] == forged_mapping["source"]
    )
    forged_mapping["source_page"] = 2 if forged_mapping["source_page"] == 1 and source_page_count >= 2 else 1
    forged = runner.run(
        runner.state,
        ["validate", "--library", str(case / "candidate"), "--plan", str(write_json(case / "forged-plan.json", forged_plan))],
        expected=1,
    )
    require(any(item["code"] == "SPECIAL_RANGE_INCOMPLETE" for item in forged["errors"]), "forged Special source-page mapping was accepted")
    validation = validate(runner, case / "candidate", write_json(case / "plan.json", special_plan))
    review_root = case / "review-copies"
    review_hashes: dict[str, str] = {}
    review_by_source: dict[str, Path] = {}
    for source_path in (source_a, source_b):
        review_path = review_root / source_path.name
        review_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, review_path)
        review_by_source[str(source_path.resolve())] = review_path
        review_hashes[str(review_path)] = sha256(review_path)
    kept_by_output = {page["output_page"]: page for page in result["kept_pages"]}
    source_page_evidence = {str(path.resolve()): image_evidence(path) for path in (source_a, source_b)}
    omitted_rows: list[dict[str, Any]] = []
    for page in result["omitted_pages"]:
        target_page = page["dedupe_target"]["output_page"]
        kept_target = kept_by_output[target_page]
        review_path = review_by_source[page["source"]]
        omitted_rows.append({
            "source": page["source"],
            "source_page": page["source_page"],
            "source_page_sha256": source_page_evidence[page["source"]][page["source_page"] - 1]["sha256"],
            "duplicate_target": {
                "output_page": target_page,
                "output_sha256": kept_target["byte_sha256"],
                "perceptual_hash": kept_target["perceptual_hash"],
            },
            "evidence": page["evidence"],
            "review_copy_preserved": True,
            "review_copy": str(review_path),
            "review_copy_sha256": sha256(review_path),
        })
    state_for_audit = {
        "source_root": str(case / "sources"),
        "review_root": str(review_root),
        "review_copy_hashes": review_hashes,
        "special_deduplication": [{
            "output": output.relative_to(case / "candidate").as_posix(),
            "result": "resolved",
            "visual_review_complete": True,
            "audit_record_id": "synthetic-special-promotion-audit",
            "source_hashes": {str(path): sha256(path) for path in (source_a, source_b)},
            "omitted_pages": omitted_rows,
            "review_copy_status": "preserved",
        }],
    }
    audit_spec = importlib.util.spec_from_file_location("manga_organizer_special_audit_test", runner.state)
    require(audit_spec is not None and audit_spec.loader is not None, "could not load Special audit validator")
    audit_module = importlib.util.module_from_spec(audit_spec)
    sys.modules[audit_spec.name] = audit_module
    audit_spec.loader.exec_module(audit_module)
    special_audit_errors = audit_module._special_audit_errors(validation, state_for_audit, case)
    require(not special_audit_errors, f"valid Special promotion audit was rejected: {special_audit_errors}")
    forged_state = json.loads(json.dumps(state_for_audit))
    forged_state["special_deduplication"][0]["omitted_pages"][0]["source_page_sha256"] = "0" * 64
    require(audit_module._special_audit_errors(validation, forged_state, case), "forged omitted-page audit was accepted")
    return {
        "merged_into_main_series": True,
        "omitted_duplicates": result["deduplication"]["omitted_count"],
        "unique_pages": result["output_page_count"],
        "visual_review_records": len(visual_review),
        "promotion_audit_bound_to_real_pages": True,
    }


def case_incomplete_interlude(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "07-incomplete-interlude"
    source = write_cbz(case / "source.cbz", [image_bytes(801), image_bytes(802), image_bytes(803)], volume="1")
    output_dir = case / "candidate" / SERIES
    plan = write_json(
        case / "boundaries.json",
        {
            "source": str(source),
            "source_page_count": 3,
            "identity_policy": "continuous-chapter",
            "visual_review_complete": True,
            "boundaries": [
                {"kind": "chapter", "start": 1, "end": 2, "output": str(output_dir / f"{SERIES} Ch.001.cbz"), "number": "1"},
                {
                    "kind": "special",
                    "start": 3,
                    "end": 4,
                    "output": str(output_dir / f"{SERIES} SP01.cbz"),
                    "number": "SP01",
                    "identity": {"Title": "间章"},
                    "complete_independent_range": True,
                    "confidence": "high",
                    "evidence": ["synthetic printed contents and visible heading"],
                },
            ],
        },
    )
    result = runner.run(
        runner.transform,
        ["split", "--source", str(source), "--boundaries", str(plan)],
        expected=2,
    )
    require(result["error"]["code"] == "BOUNDARY_OUT_OF_RANGE", "missing interlude pages were not rejected")
    require(not list(case.rglob("*SP*.cbz")), "an incomplete Special was generated")
    return {"special_generated": False, "reported": "table of contents exceeds scanned range"}


def case_ignored_damage(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "08-ignored-damage"
    source = case / "sources" / "damaged-source.bin"
    review = case / "_Needs Review" / "damaged-source.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not-a-valid-archive")
    review.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, review)
    write_cbz(case / "candidate" / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(1)], number="1")
    decision = {
        "user_ignored_damaged_items": [{"source": str(source), "review_copy": str(review), "decision": "ignore"}],
        "skipped_items": [{"source": str(source), "reason": "user_ignored_damaged_item"}],
    }
    write_json(case / "decision-resolution.json", decision)
    validate(runner, case / "candidate", write_json(case / "plan.json", {"identity_policy": "continuous-chapter"}))
    require(source.exists() and review.exists() and sha256(source) == sha256(review), "damaged source/review copy was not preserved")
    return {"blocked_other_items": False, "formal_archive_for_damage": False, "source_and_review_preserved": True}


def case_resume(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "09-resume"
    source_root = case / "sources"
    formal = case / "formal"
    staging = case / "persistent-staging"
    cache = case / "disposable-cache"
    source_file = source_root / "source.bin"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"synthetic-source")
    write_cbz(formal / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(1)], number="1")
    formal_archive = next(formal.rglob("*.cbz"))
    cache.mkdir(parents=True)
    state_payload = {
        "schema_version": 1,
        "run_id": "synthetic-resume-run",
        "status": "paused",
        "source_root": str(source_root),
        "formal_library": str(formal),
        "backup_directory": str(case / "backups"),
        "staging_path": str(staging),
        "current_phase": "building-candidate",
        "last_complete_unit": "archive-0001",
        "completed_archives": ["archive-0001"],
        "pending_archives": ["archive-0002"],
        "source_hashes": {str(source_file): sha256(source_file)},
        "formal_library_baseline": {"root": str(formal), "archives": {formal_archive.relative_to(formal).as_posix(): sha256(formal_archive)}},
        "chapter_boundaries": {"archive-0001": {"confirmed": True}},
        "ocr_permissions": {"scope": "archive-0001", "authorized": True},
        "ocr_review_conclusions": {"archive-0001": "visually-confirmed"},
        "primary_editions": {"chapter-1": "edition-a"},
        "ignored_damaged_items": ["damaged-item"],
        "special_deduplication": {"special-1": "confirmed"},
        "locked_metadata": {"Series": SERIES},
        "candidate_library_status": "rebuildable",
        "candidate_manifest": {},
        "unaffected_archive_hashes": {formal_archive.relative_to(formal).as_posix(): sha256(formal_archive)},
        "affected_formal_archives": [],
        "profile": "kavita-chapter",
        "identity_policy": "continuous-chapter",
        "tool_fingerprint": {
            "library_state.py": sha256(runner.state),
            "cbz_transform.py": sha256(runner.transform),
        },
        "lock_owner": "synthetic-test",
        "filesystem_capabilities": {"atomic_rename": True},
        "disposable_caches": [str(cache)],
    }
    controls = case / "controls"
    config_path = write_json(controls / "config.json", {"profile": "kavita-chapter"})
    plan_path = write_json(controls / "plan.json", {"identity_policy": "continuous-chapter"})
    decision_path = write_json(controls / "decision-resolution.json", {"decisions": ["confirmed"]})
    state_payload.update({
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "plan_path": str(plan_path),
        "plan_sha256": sha256(plan_path),
        "decision_log_path": str(decision_path),
        "decision_log_sha256": sha256(decision_path),
        "review_copy_hashes": {},
    })
    state_input = write_json(case / "state-input.json", state_payload)
    state_path = case / "_reports" / "resume-state.json"
    state_path.parent.mkdir(parents=True)
    checkpoint = runner.run(
        runner.state,
        ["checkpoint", "--state", str(state_path), "--state-json", str(state_input), "--execute"],
    )
    require(checkpoint["written"], "resume state was not written")
    shutil.rmtree(cache)
    resume = runner.run(runner.state, ["resume-check", "--state", str(state_path)])
    require(resume["ok"] and resume["repeat_decisions"] is False, "resume check did not preserve decisions")
    actions = {item["action"] for item in resume["actions"]}
    require("rebuild-staging-from-checkpoint" in actions and "rebuild-disposable-cache" in actions, "resume actions differ")
    reused = set(resume["reused_decision_sections"])
    require({"ocr_permissions", "ocr_review_conclusions", "chapter_boundaries", "primary_editions", "ignored_damaged_items", "special_deduplication", "locked_metadata"} <= reused, "resolved decisions were not reused")
    valid_state_text = state_path.read_text(encoding="utf-8")
    corrupted = json.loads(valid_state_text)
    corrupted["current_phase"] = "tampered-without-checksum-update"
    write_json(state_path, corrupted)
    rejected = runner.run(runner.state, ["resume-check", "--state", str(state_path)], expected=1)
    require(any(item["code"] == "resume_state_checksum_mismatch" for item in rejected["errors"]), "corrupt resume state was accepted")
    state_path.write_text(valid_state_text, encoding="utf-8")
    return {"persistent_state": True, "cache_loss_recoverable": True, "decisions_reasked": False, "state_checksum_enforced": True}


def case_promotion(root: Path, runner: Runner) -> dict[str, Any]:
    case = root / "10-promotion"
    unsafe_library = case / "unsafe-symlink-candidate"
    write_cbz(unsafe_library / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(99)], number="1")
    os.symlink(str(unsafe_library / SERIES), str(unsafe_library / "linked-series"))
    unsafe_result = runner.run(
        runner.state,
        ["validate", "--library", str(unsafe_library), "--plan", str(write_json(case / "unsafe-plan.json", {"identity_policy": "continuous-chapter"}))],
        expected=1,
    )
    require(any(item["code"] == "library_tree_symlink" for item in unsafe_result["errors"]), "candidate-tree symlink was accepted")
    final = case / "formal"
    candidate = case / "candidate"
    backups = case / "backups"
    backups.mkdir(parents=True)
    old_archive = write_cbz(final / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(1)], number="1")
    old_hash = sha256(old_archive)
    write_cbz(candidate / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(1)], number="1")
    write_cbz(candidate / SERIES / f"{SERIES} Ch.002.cbz", [image_bytes(2)], number="2")
    write_checksums(candidate)
    source_root = case / "sources"
    source_file = source_root / "source.bin"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"promotion-source")
    candidate_archives = {
        item.relative_to(candidate).as_posix(): sha256(item) for item in candidate.rglob("*.cbz")
    }
    formal_archives = {old_archive.relative_to(final).as_posix(): old_hash}
    state_payload = promotion_state_payload(
        label="promotion",
        source_root=source_root,
        source_file=source_file,
        final=final,
        backups=backups,
        candidate=candidate,
        formal_archives=formal_archives,
        candidate_archives=candidate_archives,
        tool_root=runner.state.parent.parent,
    )
    state_input = write_json(case / "promotion-state-input.json", state_payload)
    state_path = candidate / "_reports" / "resume-state.json"
    checkpoint = runner.run(
        runner.state,
        ["checkpoint", "--state", str(state_path), "--state-json", str(state_input), "--execute"],
    )
    require(checkpoint["written"], "promotion checkpoint was not written")
    common = [
        "promote", "--candidate", str(candidate), "--final", str(final), "--backup-root", str(backups),
        "--state", str(state_path),
    ]
    preview = runner.run(runner.state, common + ["--dry-run"])
    require(preview["ok"] and not preview["executed"], "promotion dry-run differs")
    result = runner.run(runner.state, common + ["--execute"])
    require(result["ok"] and result["executed"] and result["postcheck"]["ok"], "promotion/postcheck failed")
    backup = Path(result["backup_path"])
    require(backup.is_dir() and final.is_dir() and not candidate.exists(), "atomic paths differ after promotion")
    backed_archive = next(backup.rglob("*.cbz"))
    require(sha256(backed_archive) == old_hash, "backup cannot restore the old formal library")
    promoted_state = Path(result["state"])
    require(json.loads(promoted_state.read_text(encoding="utf-8"))["status"] == "validated-final", "final state differs")

    rollback_case = case / "fault-injection"
    rollback_final = rollback_case / "formal"
    rollback_candidate = rollback_case / "candidate"
    rollback_backups = rollback_case / "backups"
    rollback_backups.mkdir(parents=True)
    rollback_old = write_cbz(rollback_final / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(11)], number="1")
    rollback_old_hash = sha256(rollback_old)
    write_cbz(rollback_candidate / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(11)], number="1")
    write_cbz(rollback_candidate / SERIES / f"{SERIES} Ch.002.cbz", [image_bytes(12)], number="2")
    write_checksums(rollback_candidate)
    rollback_source_root = rollback_case / "sources"
    rollback_source = rollback_source_root / "source.bin"
    rollback_source.parent.mkdir(parents=True)
    rollback_source.write_bytes(b"rollback-source")
    rollback_candidate_archives = {
        item.relative_to(rollback_candidate).as_posix(): sha256(item) for item in rollback_candidate.rglob("*.cbz")
    }
    rollback_payload = promotion_state_payload(
        label="rollback",
        source_root=rollback_source_root,
        source_file=rollback_source,
        final=rollback_final,
        backups=rollback_backups,
        candidate=rollback_candidate,
        formal_archives={rollback_old.relative_to(rollback_final).as_posix(): rollback_old_hash},
        candidate_archives=rollback_candidate_archives,
        tool_root=runner.state.parent.parent,
    )
    rollback_input = write_json(rollback_case / "state-input.json", rollback_payload)
    rollback_state = rollback_candidate / "_reports" / "resume-state.json"
    checkpoint = runner.run(
        runner.state,
        ["checkpoint", "--state", str(rollback_state), "--state-json", str(rollback_input), "--execute"],
    )
    require(checkpoint["written"], "rollback checkpoint was not written")
    spec = importlib.util.spec_from_file_location("manga_organizer_library_state_fault_test", runner.state)
    require(spec is not None and spec.loader is not None, "could not load library_state for failure injection")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original_validate = module.validate_library
    validation_calls = 0

    def fail_formal_postcheck(*call_args: Any, **call_kwargs: Any) -> dict[str, Any]:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            return {"ok": False, "errors": [{"code": "synthetic_postcheck_failure"}], "summary": {}}
        return original_validate(*call_args, **call_kwargs)

    module.validate_library = fail_formal_postcheck
    rollback_result = module.promote_library(
        rollback_candidate,
        rollback_final,
        rollback_backups,
        rollback_state,
        True,
    )
    module.validate_library = original_validate
    require(not rollback_result["ok"] and rollback_result["rolled_back"], "postcheck failure did not roll back")
    require(rollback_result.get("rollback_validation") is True, "restored formal library was not revalidated")
    require(sha256(next(rollback_final.rglob("*.cbz"))) == rollback_old_hash, "rollback did not restore the old library")
    require(rollback_result["failed_candidate_preserved"], "failed candidate was not preserved")
    require(any(item["code"] == "PROMOTION_POSTCHECK_FAILED" for item in rollback_result["errors"]), "stable postcheck error code was not emitted")
    journal = json.loads(Path(rollback_result["promotion_journal"]).read_text(encoding="utf-8"))
    require(journal["status"] == "rollback-complete", "rollback journal was not completed")

    crash_case = case / "interrupted-rename-recovery"
    crash_final = crash_case / "formal"
    crash_candidate = crash_case / "candidate"
    crash_backups = crash_case / "backups"
    crash_backups.mkdir(parents=True)
    crash_old = write_cbz(crash_final / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(21)], number="1")
    write_cbz(crash_candidate / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(21)], number="1")
    write_cbz(crash_candidate / SERIES / f"{SERIES} Ch.002.cbz", [image_bytes(22)], number="2")
    write_checksums(crash_candidate)
    crash_source_root = crash_case / "sources"
    crash_source = crash_source_root / "source.bin"
    crash_source.parent.mkdir(parents=True)
    crash_source.write_bytes(b"interrupted-promotion-source")
    crash_candidate_archives = {
        item.relative_to(crash_candidate).as_posix(): sha256(item) for item in crash_candidate.rglob("*.cbz")
    }
    crash_payload = promotion_state_payload(
        label="interrupted-recovery",
        source_root=crash_source_root,
        source_file=crash_source,
        final=crash_final,
        backups=crash_backups,
        candidate=crash_candidate,
        formal_archives={crash_old.relative_to(crash_final).as_posix(): sha256(crash_old)},
        candidate_archives=crash_candidate_archives,
        tool_root=runner.state.parent.parent,
    )
    crash_input = write_json(crash_case / "state-input.json", crash_payload)
    crash_state = crash_candidate / "_reports" / "resume-state.json"
    checkpoint = runner.run(
        runner.state,
        ["checkpoint", "--state", str(crash_state), "--state-json", str(crash_input), "--execute"],
    )
    require(checkpoint["written"], "interrupted-promotion checkpoint was not written")
    original_rename = module.os.rename
    rename_count = 0

    def interrupt_second_rename(source_path: str, target_path: str) -> None:
        nonlocal rename_count
        rename_count += 1
        if rename_count == 2:
            raise KeyboardInterrupt("synthetic interruption between atomic renames")
        original_rename(source_path, target_path)

    module.os.rename = interrupt_second_rename
    try:
        try:
            module.promote_library(crash_candidate, crash_final, crash_backups, crash_state, True)
        except KeyboardInterrupt:
            pass
        else:
            raise TestFailure("synthetic promotion interruption did not occur")
    finally:
        module.os.rename = original_rename
    crash_journal = crash_state.parent / "promotion-journal.json"
    require(crash_journal.is_file() and crash_candidate.is_dir() and not crash_final.exists(), "interrupted promotion facts differ")
    recovery_preview = runner.run(
        runner.state,
        ["recover-promotion", "--journal", str(crash_journal), "--state", str(crash_state), "--dry-run"],
    )
    require(recovery_preview["action"] == "continue-candidate-promotion-then-postcheck", "recovery action was not deterministic")
    recovered = runner.run(
        runner.state,
        ["recover-promotion", "--journal", str(crash_journal), "--state", str(crash_state), "--execute"],
    )
    require(recovered["ok"] and recovered["executed"] and crash_final.is_dir(), "interrupted promotion did not recover")
    require(json.loads(Path(recovered["journal"]).read_text(encoding="utf-8"))["status"] == "validated-final", "recovery journal was not finalized")

    postrename_case = case / "interrupted-after-candidate-rename"
    postrename_final = postrename_case / "formal"
    postrename_candidate = postrename_case / "candidate"
    postrename_backups = postrename_case / "backups"
    postrename_backups.mkdir(parents=True)
    postrename_old = write_cbz(postrename_final / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(31)], number="1")
    write_cbz(postrename_candidate / SERIES / f"{SERIES} Ch.001.cbz", [image_bytes(31)], number="1")
    write_cbz(postrename_candidate / SERIES / f"{SERIES} Ch.002.cbz", [image_bytes(32)], number="2")
    write_checksums(postrename_candidate)
    postrename_source_root = postrename_case / "sources"
    postrename_source = postrename_source_root / "source.bin"
    postrename_source.parent.mkdir(parents=True)
    postrename_source.write_bytes(b"postrename-interruption-source")
    postrename_candidate_archives = {
        item.relative_to(postrename_candidate).as_posix(): sha256(item)
        for item in postrename_candidate.rglob("*.cbz")
    }
    postrename_payload = promotion_state_payload(
        label="postrename-interruption",
        source_root=postrename_source_root,
        source_file=postrename_source,
        final=postrename_final,
        backups=postrename_backups,
        candidate=postrename_candidate,
        formal_archives={postrename_old.relative_to(postrename_final).as_posix(): sha256(postrename_old)},
        candidate_archives=postrename_candidate_archives,
        tool_root=runner.state.parent.parent,
        controls_root=postrename_candidate / "_reports" / "controls",
    )
    postrename_input = write_json(postrename_case / "state-input.json", postrename_payload)
    postrename_state = postrename_candidate / "_reports" / "resume-state.json"
    checkpoint = runner.run(
        runner.state,
        ["checkpoint", "--state", str(postrename_state), "--state-json", str(postrename_input), "--execute"],
    )
    require(checkpoint["written"], "postrename-interruption checkpoint was not written")
    postrename_original_validate = module.validate_library
    postrename_validation_calls = 0

    def interrupt_formal_postcheck(*call_args: Any, **call_kwargs: Any) -> dict[str, Any]:
        nonlocal postrename_validation_calls
        postrename_validation_calls += 1
        if postrename_validation_calls == 2:
            raise KeyboardInterrupt("synthetic interruption after candidate rename")
        return postrename_original_validate(*call_args, **call_kwargs)

    module.validate_library = interrupt_formal_postcheck
    try:
        try:
            module.promote_library(
                postrename_candidate,
                postrename_final,
                postrename_backups,
                postrename_state,
                True,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise TestFailure("synthetic postrename interruption did not occur")
    finally:
        module.validate_library = postrename_original_validate
    relocated_state = postrename_final / "_reports" / "resume-state.json"
    relocated_journal = postrename_final / "_reports" / "promotion-journal.json"
    require(
        relocated_state.is_file() and relocated_journal.is_file() and postrename_final.is_dir() and not postrename_candidate.exists(),
        "postrename interruption did not leave recoverable formal controls",
    )
    recovery_preview = runner.run(
        runner.state,
        ["recover-promotion", "--journal", str(relocated_journal), "--state", str(relocated_state), "--dry-run"],
    )
    require(recovery_preview["action"] == "run-formal-postcheck-and-finalize", "postrename recovery action was not deterministic")
    postrename_recovered = runner.run(
        runner.state,
        ["recover-promotion", "--journal", str(relocated_journal), "--state", str(relocated_state), "--execute"],
    )
    require(postrename_recovered["ok"] and postrename_recovered["executed"], "postrename interruption did not recover")
    require(
        json.loads(Path(postrename_recovered["journal"]).read_text(encoding="utf-8"))["status"] == "validated-final",
        "postrename recovery journal was not finalized",
    )
    mismatched = runner.run(
        runner.state,
        ["recover-promotion", "--journal", str(Path(recovered["journal"])), "--state", str(Path(postrename_recovered["state"])), "--dry-run"],
        expected=1,
    )
    require(
        any(item["code"] in {"promotion_journal_run_mismatch", "promotion_journal_state_mismatch"} for item in mismatched["errors"]),
        "a journal from another run was accepted",
    )
    return {
        "backup_exists": True,
        "promoted": True,
        "formal_path_postcheck": True,
        "old_library_recoverable": True,
        "injected_postcheck_failure_rolled_back": True,
        "failed_candidate_preserved": True,
        "interrupted_rename_recovered": True,
        "postrename_control_paths_recovered": True,
        "symlink_candidate_rejected": True,
        "cross_run_journal_rejected": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", help="Empty or non-existing directory used only for synthetic artifacts.")
    parser.add_argument("--keep", action="store_true", help="Keep an automatically created temporary work directory.")
    parser.add_argument("--dry-run", action="store_true", help="Emit the planned synthetic cases without creating artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    case_names = [
        "continuous-chapter identity",
        "verified volume fallback",
        "mixed chapter and fallback",
        "confirmed source gap",
        "identity normalization integrity",
        "Special merge and deduplication",
        "incomplete interlude",
        "ignored damaged item",
        "pause and resume",
        "promotion and postcheck",
    ]
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "synthetic_only": True, "planned_cases": case_names}, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if Image is None or ImageDraw is None:
        print(json.dumps({"ok": False, "synthetic_only": True, "error": {"code": "PILLOW_REQUIRED", "message": "Pillow is required for executable synthetic tests"}}, ensure_ascii=False, sort_keys=True, indent=2))
        return 2
    managed = args.workdir is None
    if managed:
        workdir = Path(tempfile.mkdtemp(prefix="manga-organizer-synthetic-"))
    else:
        workdir = Path(args.workdir).expanduser().resolve(strict=False)
        if workdir.exists() and any(workdir.iterdir()):
            print(json.dumps({"ok": False, "error": "--workdir must be empty"}, ensure_ascii=False))
            return 2
        workdir.mkdir(parents=True, exist_ok=True)
    runner = Runner(Path(__file__).resolve().parent.parent)
    cases = [
        ("continuous-chapter identity", case_continuous_identity),
        ("verified volume fallback", case_volume_fallback),
        ("mixed chapter and fallback", case_mixed_packaging),
        ("confirmed source gap", case_intentional_gap),
        ("identity normalization integrity", case_ten_archive_normalization),
        ("Special merge and deduplication", case_special_merge),
        ("incomplete interlude", case_incomplete_interlude),
        ("ignored damaged item", case_ignored_damage),
        ("pause and resume", case_resume),
        ("promotion and postcheck", case_promotion),
    ]
    require([name for name, _ in cases] == case_names, "synthetic case registry differs from dry-run plan")
    results: list[dict[str, Any]] = []
    ok = True
    try:
        for name, function in cases:
            try:
                details = function(workdir, runner)
                results.append({"name": name, "ok": True, "details": details})
            except Exception as exc:  # Emit the completed case list in one JSON report.
                ok = False
                results.append({"name": name, "ok": False, "error": str(exc)})
                break
        output = {"ok": ok, "synthetic_only": True, "case_count": len(results), "cases": results}
        if args.keep or not managed:
            output["workdir"] = str(workdir)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if ok else 1
    finally:
        if managed and not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
