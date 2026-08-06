# Kavita identity, boundary, and fallback rules

## Contents

- Series identity policies
- Boundary evidence
- Coverage and visual review
- Verified volume fallback
- Missing, damaged, and incomplete content
- ComicInfo and filenames
- Boundary report schema

## Series identity policies

Choose one policy for the whole series before naming any file:

| Policy | Use | Normal chapter identity |
|---|---|---|
| `continuous-chapter` | Default; continuous story numbering across source volumes | `<Series> Ch.<chapter:03>.cbz`; ComicInfo `Number`; omit `Volume` |
| `volume-aware-chapter` | Only on explicit user request | `<Series> Vol.<volume:02> Ch.<chapter:03>.cbz`; write `Volume` and `Number` |
| `volume-only` | Explicit whole-series collection by volume | `<Series> v<volume:02>.cbz`; write `Volume`; omit `Number` |

Under `continuous-chapter`, retain source volume only in ComicInfo `Notes`, `plan.json`, and `source-provenance.csv`. Never let source grouping become Kavita identity accidentally.

Audit and stop automatic finalization for `SERIES_IDENTITY_MIX`, `PARTIAL_VOLUME_TAGGING`, `KAVITA_VOLUME_JUMP_RISK`, `DUPLICATE_CHAPTER_IDENTITY`, or `UNDOCUMENTED_VOLUME_FALLBACK`. A documented verified fallback volume may coexist with continuous chapters and is not an identity mix: normal chapters have `Number` and no `Volume`; fallbacks have `Volume` and no `Number`.

Do not place media directly at the Kavita library root. Keep one series in one directory, with `Specials/` below it.

## Boundary evidence

Inspect permitted evidence in this order, weighing conflicts rather than accepting the first marker:

1. Embedded contents, PDF bookmarks, and embedded metadata.
2. EPUB OPF manifest, spine order, NCX, navigation document, and embedded metadata.
3. Visible contents pages, chapter title pages, chapter captions, and structural changes.
4. Filename, directory name, and existing ComicInfo.
5. Adjacent chapter sequence and reliable volume-to-chapter mapping.
6. Reliable external chapter order/page information.
7. OCR only after explicit user authorization.

For printed contents-page numbers, determine the offset between printed numbering and archive/scan indices from multiple anchors. Never copy printed numbers directly into ranges without calibration.

Treat a lone chapter-number page as fallible. If it conflicts with contents, surrounding sequence, narrative continuity, or reliable external order, record the conflict and choose a boundary only from the combined evidence.

For EPUB, follow the spine for reading order; manifest order alone is insufficient. Confirm that NCX/navigation targets exist and inspect visible headings at candidate boundaries. Preserve reflowable text EPUB instead of converting automatically.

OCR authorization must include source scope and permitted purpose in `decision-resolution.csv` and `resume-state.json`. Visually verify OCR conclusions. Reuse confirmed permission and boundaries after resume; do not ask again unless scope or source hash changed. Do not use OCR when local contents/headings suffice.

## Coverage and visual review

For every split source require:

- inclusive, contiguous page spans;
- no overlap;
- every page assigned exactly once;
- `sum(end - start + 1) == source_page_count`;
- visual review of first page, last page, and pages immediately around every boundary.

When splitting an existing CBZ, read each image member once, copy its bytes without rendering, keep source page order, record source-member-to-output-member mapping, and verify SHA-256 equality for every output image.

Assign cover, contents, and other front matter to the first chapter. Assign end matter, production information, advertisements, copyright, and release pages to the last chapter unless a complete independent Special is proven. Preserve repeated advertisements and credits unless the user explicitly requests deletion.

## Verified volume fallback

After exhausting allowed evidence, create one volume CBZ instead of Review when all conditions hold:

1. series identity is high-confidence;
2. volume number is confirmed;
3. the file contains exactly one complete volume;
4. every page is readable;
5. the natural-order `1..N` span is covered exactly once;
6. no overlap or omission exists;
7. no chapter number or range is fabricated;
8. the result passes normal archive validation.

Also assign a stable `fallback_id` and record a machine-readable cross-package overlap audit against every active chapter, Special, and other fallback in the series. Derive exact matches and `dhash-88-color` candidates from the actual decoded pages; record the threshold, compared canonical identities/archive hashes, exact page SHA-256 comparisons, every calculated candidate, and one reviewer/reason/hash-bound decision for each candidate. Use a result of `no-overlap` or `resolved` and link its stable audit-record ID. A report-supplied empty candidate list is valid only when recomputation also finds none; an incomplete or unresolved audit is ineligible for formal placement.

Output `<Series> v<volume:02>.cbz`; write ComicInfo `Volume`; omit `Number`; use a reliable Chinese volume `Title`; set `packaging_mode: volume-fallback`; and record attempted evidence plus the reason reliable chapter boundaries could not be established.

Never omit a complete confirmed volume solely because it cannot be split. Never disguise it as one long chapter, divide it evenly, or estimate boundaries. Mark every fallback in preflight, identity audit, boundary report, and final report. `continuous-chapter` plus documented fallback is permitted by `mixed_packaging_policy: allow-documented-volume-fallback`.

Recheck a fallback when new contents/headings/evidence or OCR permission becomes available. Fallback is reversible, not a permanent ban on later splitting.

## Missing, damaged, and incomplete content

Distinguish these states:

- Missing source volume/chapter: preserve the real chapter gap; create nothing.
- Complete source volume without reliable boundaries: create a verified volume fallback.
- Present but damaged/incomplete source: preserve source and review copy; create no formal archive.

Only route to `_Needs Review` for uncertain series/volume identity, possible mixed volumes, corruption/missing pages, unverifiable full coverage, or unresolved duplicate identity.

A contents entry does not prove pages are present. If a listed interlude begins outside the scan range or lacks a complete independent span, create no Special and report `contents-listed-but-scan-absent`.

## ComicInfo and filenames

| Unit | Filename | `Number` | `Volume` | `Format` |
|---|---|---|---|---|
| Continuous chapter | `<Series> Ch.<chapter:03>.cbz` | Actual chapter | Omit | Evidence-based |
| Volume-aware chapter | `<Series> Vol.<volume:02> Ch.<chapter:03>.cbz` | Actual chapter | Confirmed volume | Evidence-based |
| Volume fallback/volume-only | `<Series> v<volume:02>.cbz` | Omit | Confirmed volume | Not `Special` |
| Special | `<Series> SP<index:02> <Title>.cbz` | `SPxx` | Omit | `Special` |

Keep `Series`, `LocalizedSeries`, and `SeriesSort` identical and Chinese. Use root `ComicInfo.xml`, actual `PageCount`, correct reading direction, and safe XML parsing.

## Boundary report schema

Each `chapter-boundaries.json` source record must contain `source`, `source_sha256`, `source_page_count`, `boundary_method`, `coverage`, `packaging_mode`, `fallback_reason`, `attempted_evidence`, `ocr_used`, `units`, and `deliberate_missing_ranges`. If an older consumer requires `intentional_missing_ranges`, serialize it only as a mirrored compatibility alias of the same records; never let the two diverge.

A missing-source record must use `record_kind: missing-source` and distinguish `missing_scope_kind: chapter-range` from `missing_scope_kind: source-volume`. Because there is no source file to hash or count, keep the required keys as `source: <expected locator>`, `source_sha256: null`, and `source_page_count: null`; this null exception is allowed only for `record_kind: missing-source`. For an absent source volume whose chapter mapping is unknown, store the confirmed source volume and `chapter_range: null`, then emit `UNMAPPED_SOURCE_ABSENCE`; do not convert it to `CONFIRMED_SOURCE_GAP`. A documented fallback may use `fallback_covered_range` only when reliable mapping evidence supports the range. Otherwise record `coverage_relation: unnumbered-volume-coverage`; it does not prove or silently close a numbered chapter gap.

Each unit must contain `output`, `kind`, `chapter` or `special`, `start`, `end`, `page_count`, `evidence`, `confidence`, `includes_front_matter`, `includes_end_matter`, `includes_credits`, and `includes_advertisements`. For fallback, use one `1..N` unit with no chapter. A Special additionally records verified source components and full source-file SHA-256 values, included source pages, and a continuous output-page mapping whose source page SHA-256 equals both the preserved source page and final CBZ page. Merged Specials also bind every omitted page to the real source page, retained output target, exact or visually confirmed perceptual evidence, and a review copy listed in the verified review manifest.

Record evidence conflicts, chosen rationale, visual-review pages/results, gaps, overlaps, assigned-page total, and exact-coverage boolean. Verify current official Kavita parsing behavior before relying on edge cases.
