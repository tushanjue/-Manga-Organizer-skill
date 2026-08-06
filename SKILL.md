---
name: manga-organizer
description: Inspect, organize, split, normalize, resume, validate, and safely replace manga libraries for Kavita. Use for mixed images/PDF/EPUB/CBZ archives; continuous chapter identities; Kavita volume-jump repair; removal of partial Volume tagging; splitting volume CBZs into chapters; verified volume fallback when boundaries are unavailable; preserving real source gaps; merging and deduplicating official companion material into Specials; resuming paused Manga Organizer work; reorganizing an existing Manga Organizer Output; or performing explicit, non-destructive identity normalization with backups and member-level audits.
---

# Manga Organizer

Turn a source folder or existing Manga Organizer output into a verified Kavita library without silently damaging originals.

## 1. Inputs and defaults

Require only `source`. Accept optional `output`, `profile`, `identity_policy`, `language`, `reading_direction`, `metadata`, `mode`, `update_mode`, and `config`.

Use these defaults:

```yaml
profile: kavita-chapter
identity_policy: continuous-chapter
unsplittable_volume_policy: verified-volume-fallback
mixed_packaging_policy: allow-documented-volume-fallback
companion_material_policy: review
language: zh-Hans
reading_direction: rtl
metadata: bangumi
mode: auto-safe
update_mode: new-library
preserve_source: true
overwrite_source: false
resume_enabled: true
```

Create an omitted output as a sibling named `<source-folder-name> - Manga Organizer Output`; never put output inside source.

## 2. Required references

Read only the references needed for the task, but read each selected file completely:

- Read `references/KAVITA_FORMAT.md` for identity policies, chapter boundaries, EPUB/PDF evidence, volume fallback, page coverage, and Kavita naming.
- Read `references/IDENTITY_REORGANIZATION.md` before updating an existing library, repairing volume jumps, preserving real gaps, selecting editions, or merging/deduplicating Specials.
- Read `references/RESUME_AND_RECOVERY.md` for pause/resume, persistent staging, backups, promotion, postcheck, and rollback.
- Read `references/COMICINFO_MAPPING.md` before creating or changing ComicInfo.
- Read `references/METADATA_POLICY.md` for Chinese fields, Japanese Summary translation, Publisher, Tags, and locked metadata.
- Read `references/BANGUMI_MATCHING.md` before network metadata matching.
- Use codes from `references/ISSUE_CODES.md` in plans and reports.

## 3. Non-negotiable safety

1. Treat every source as read-only unless the user explicitly authorizes otherwise.
2. Run full preflight and show the proposed series-level identity plan before writing.
3. Never overwrite, delete, move, rename, renumber, or replace source material silently.
4. Build in a persistent sibling staging/checkpoint directory; use `/private/tmp` only for reconstructable caches and extraction.
5. Back up an existing formal library outside that library before any replacement.
6. Finish the current archive-sized atomic unit before pausing; never leave a half-written CBZ or half-completed replacement.
7. Use temporary files plus `os.replace` for single-archive/state writes and atomic directory renames for promotion.
8. Never run Git operations as part of manga organization.
9. Never claim completion before archive, identity, source-hash, backup, and formal-path postchecks pass.
10. Continue independent safe work when some items need review; preserve review copies and source files.

## 4. Update modes

Choose one mode before planning:

- `new-library`: build a new output without changing source.
- `metadata-refresh`: change only non-identity ComicInfo fields. Preserve filename, directory, `Series`, `LocalizedSeries`, `SeriesSort`, `Volume`, `Number`, `Format`, member bytes, and member order.
- `identity-normalization`: enable only when the user explicitly asks to repair Kavita jumps, continuous chapter identity, series merging, or Special reclassification. Permit only confirmed filename/directory and identity-field changes; preserve every non-ComicInfo byte, page order, member order, and source.

Generate the member-level and series audits required by `references/IDENTITY_REORGANIZATION.md` for identity normalization.

## 5. Series-level identity planning

Select one policy per series, never independently per file:

- `continuous-chapter` (default): name normal chapters `<Series> Ch.<chapter:03>.cbz`; write `Number`; omit `Volume`. Preserve source volume in `Notes`, `plan.json`, and `source-provenance.csv` only.
- `volume-aware-chapter`: use `<Series> Vol.<volume:02> Ch.<chapter:03>.cbz` and write both `Volume` and `Number`; use only on explicit user request.
- `volume-only`: collect the series as `<Series> v<volume:02>.cbz`; write `Volume` and omit `Number`.

Reject mixed normal-chapter identities, partial Volume tagging, duplicate chapter identities, and undocumented volume fallbacks. Under `continuous-chapter`, documented verified fallback volumes may coexist with chapter CBZs without being treated as a conflict: chapters use only `Ch.xxx` with no ComicInfo `Volume`; fallbacks use only `vXX` with `Volume` and no `Number`.

When explicitly normalizing an existing continuous series, remove filename `Vol.xx` and ComicInfo `Volume` from normal chapters, retain `Number`, preserve source volume as provenance, and verify every non-XML member byte and order.

## 6. Inventory and preflight

Inventory supported images, PDF, image/fixed-layout EPUB, ZIP/CBZ, RAR/CBR, 7Z/CB7, TAR/CBT, embedded metadata, archive members, page order, and actual signatures. Record SHA-256 for every source and formal-library baseline item.

Create or refresh:

```text
_reports/preflight.md
_reports/plan.json
_reports/chapter-boundaries.json
_reports/source-provenance.csv
_reports/series-identity-audit.csv
_reports/decision-resolution.csv
_reports/decision-resolution.md
_reports/resume-state.json
```

Audit each series for normal-chapter count, Special count, fallback-volume count, normal chapters carrying Volume, duplicate identities, unintended gaps, and confirmed source gaps. Persist primary-edition selection, OCR scope, ignored damaged items, locked metadata, and resolved decisions so resume does not ask again.

## 7. Chapter boundary and fallback decisions

Prefer one chapter per CBZ. Examine allowed evidence in the order and format-specific detail defined in `references/KAVITA_FORMAT.md`; never use OCR without explicit authorization recorded in both decision and resume state.

Split only when all page spans are continuous, non-overlapping, cover every source page exactly once, and the first, last, and boundary-near pages pass visual review. Calibrate printed contents-page numbers to scan indices. Resolve a suspicious single-page chapter label from multiple independent sources rather than trusting it alone.

If boundaries remain unreliable but the source is a high-confidence complete single volume with a confirmed volume number and exact readable `1..N` coverage, create a documented volume fallback. Do not send an otherwise valid complete volume to review merely because it cannot be split. Never disguise it as a long chapter or invent/evenly divide ranges.

Use `_Needs Review` only for uncertain series/volume identity, mixed volumes, damage/missing pages, incomplete coverage, or unresolved duplicate identity. Distinguish missing source, complete unsplittable source, and damaged source exactly as the Kavita reference requires.

## 8. Page and archive handling

- Natural-sort pages; preserve original image bytes unless conversion is requested.
- Reject unsafe paths, symlinks, encryption, corruption, decompression bombs, multiple/rootless ComicInfo, and XML external entities.
- For existing CBZ splits, copy image bytes without rendering; compare each output page SHA-256 to its source and record the full mapping.
- Assign cover, contents, and front matter to the first chapter; assign end matter, production, advertisements, copyright, and release pages to the last chapter unless a complete independent Special is proven.
- Preserve repeated advertisements, production pages, and scanlation information unless the user explicitly requests deletion.
- Follow EPUB OPF manifest, spine, NCX, navigation document, printed contents, and visible headings; reject automatic conversion of reflowable text EPUB.

## 9. Specials and alternate editions

Create a Special only from a complete, independent, high-confidence page range. A contents entry outside the actual scan range is report-only; never create an empty or fabricated Special.

Apply `companion_material_policy` as `merge-specials`, `separate-series`, or `review`. Merge official/high-confidence companion material into the main `Specials` folder only when the user requests it or policy allows it. Never auto-merge unofficial or uncertain doujin material.

Before merging, compare byte SHA-256, perceptual hashes, sequence, completeness, and visually review uncertain matches. Bind kept output pages and omitted-page decisions to actual preserved source/target page hashes; every omission also requires a verified review copy. Keep only complete unique content and report every omitted page, source index, duplicate target, evidence, source preservation, and review-copy status. Use stable non-conflicting `SP` numbers, `Format=Special`, reliable Chinese titles, main-series identity, and provenance in Notes/reports.

Select one high-confidence primary edition for the formal library. Put every alternate in `_Needs Review/Alternate Editions`; never overwrite, discard, or disguise it. Persist selection across resume unless source hashes or user decisions change.

## 10. Real gaps and damaged inputs

Never renumber later chapters, create placeholders, invent ranges, or treat volume numbers as chapter numbers. Record `deliberate_missing_ranges`, reason, missing source, and user confirmation in plan, boundaries, and execution reports.

Classify gaps as `unintended_gap`, `confirmed_source_gap`, or `user_ignored_damaged_item`. Record a missing source volume with an unknown chapter range as unmapped source absence, never as a guessed confirmed chapter range. A documented fallback may record `fallback_covered_range` only from reliable volume-to-chapter evidence; otherwise it remains unnumbered volume coverage and cannot silently close chapter gaps. A user-ignored damaged item must not block independent work or produce a formal CBZ; preserve source and review copy and record the decision in `decision-resolution.csv` and `skipped-items.csv`.

## 11. Metadata

Match Bangumi only at high confidence; continue safe local work when unavailable. For Chinese libraries require reliable Chinese `Series`, `LocalizedSeries`, `SeriesSort`, `Title`, and `Genre`. When only a reliable Japanese Summary exists, translate it into faithful natural Chinese, record `summary_source_language=ja` and `summary_status=translated-to-Chinese`, and keep the original in provenance/cache. Never translate Japanese work titles by default. User-locked values win.

Keep the configured proxy session-only; never write it into manga files, ComicInfo, or permanent settings.

## 12. Execution, pause, promotion, and validation

Use `scripts/cbz_transform.py` for explicit CBZ split, identity normalization, and Special merging. Use `scripts/library_state.py` for full-library validation, checkpoint/resume checks, promotion, `recover-promotion`, postcheck, and rollback. Run `--help` and a dry run before mutation; never use these tools against an unconfirmed target.

On pause, finish the current archive, atomically refresh reports and `resume-state.json`, and provide exact continuation instructions. On resume, verify source and formal-library hashes, reuse recorded OCR permissions/boundaries/decisions, rebuild missing disposable caches deterministically, and continue from the last complete unit.

Promote only after complete candidate validation. Reject symlinks in the candidate; partition every old formal archive into explicit affected and path/hash-locked unaffected sets. Bind the promotion journal to the same run, state, candidate, and formal path. Atomically rename the old formal library to a unique external timestamped backup, move the candidate into place, mark `validated-final`, and rerun postcheck from the formal path. If postcheck fails, preserve the failed candidate, restore the backup, and report failure.

Postcheck ZIP CRC, root ComicInfo uniqueness and parsing, `PageCount`, image decoding, natural order, series identity uniqueness, intentional gaps, documented fallbacks, page coverage, source/review hashes, formal CBZ count, total pages, checksums, and backup existence. Unaffected archive SHA-256 values must remain unchanged.

## 13. Final reports and completion

Also create `execution-report.md`, `bangumi-review.csv`, `skipped-items.csv`, `checksums.sha256`, `identity-normalization-member-integrity.csv`, and `reorganization-YYYYMMDD.json`, deriving `YYYYMMDD` at runtime rather than hardcoding a date.

The final report must list normal chapters, Specials, fallback volumes, real gaps, ignored damaged items, primary/alternate editions, Special dedupe results, identity-normalization count, unaffected archive count, source hashes, formal-path postcheck, and backup path.

Complete only when the source is unchanged, all supported files are inventoried, every decision and fallback is documented, every completed archive passes validation, all page coverage and identities are correct, resume state is final, formal-path postcheck passes, and recovery remains possible from the verified backup.

In `auto-safe`, unresolved `unintended_gap`, `FALLBACK_COVERED_UNNUMBERED_RANGE`, or any other REVIEW/BLOCKER prevents whole-library promotion, while independent archive construction and review-copy preservation may continue. Promotion requires reclassification from reliable evidence or an explicit persisted user decision; never infer a range merely to clear the review.
