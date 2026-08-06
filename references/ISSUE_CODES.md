# Preflight issue codes

Use stable codes in `preflight.md` and `plan.json`.

## Path and library

- `PATH001`: output is inside source tree - BLOCKER
- `PATH002`: prospective Kavita root contains media files - BLOCKER
- `PATH003`: same series split across adjacent folders - WARNING/BLOCKER
- `PATH004`: multiple series mixed in one directory - WARNING
- `PATH005`: destination is read-only or unavailable - BLOCKER
- `PATH006`: insufficient free space - BLOCKER

## Naming and identity

- `NAME001`: series cannot be inferred - BLOCKER for automatic placement
- `NAME002`: volume/chapter is ambiguous - REVIEW
- `NAME003`: filename conflicts with embedded metadata - REVIEW
- `NAME004`: illegal cross-platform filename character - SAFE_FIX
- `NAME005`: output collision - REVIEW
- `NAME006`: special cannot be classified - REVIEW

## Series identity

- `SERIES_IDENTITY_MIX`: one series mixes continuous chapters, volume-aware chapters, or undocumented volume-only identities - BLOCKER
- `PARTIAL_VOLUME_TAGGING`: only some normal chapters carry filename or ComicInfo Volume - BLOCKER
- `KAVITA_VOLUME_JUMP_RISK`: source-volume grouping may make Kavita interpret continuous chapters as skipped volumes - REVIEW/BLOCKER
- `DUPLICATE_CHAPTER_IDENTITY`: two formal-library items resolve to the same series/chapter identity - BLOCKER
- `UNDOCUMENTED_VOLUME_FALLBACK`: a volume-only item coexists with chapters without verified fallback evidence/reporting - BLOCKER
- `CONFIRMED_SOURCE_GAP`: source material is genuinely absent and the gap is documented - INFO
- `UNINTENDED_GAP`: chapter sequence has an unexplained gap - REVIEW and promotion blocker in `auto-safe`
- `USER_IGNORED_DAMAGED_ITEM`: user chose to skip a damaged item while preserving source/review copy - INFO
- `FALLBACK_COVERED_UNNUMBERED_RANGE`: a verified fallback contains story pages whose chapter identity range is unknown - INFO/REVIEW in the sequence audit, not an identity conflict
- `UNMAPPED_SOURCE_ABSENCE`: a source volume is confirmed absent but its chapter range cannot be mapped reliably - REVIEW; keep `chapter_range: null`

## Archive

- `ARC001`: extension does not match content - WARNING
- `ARC002`: archive is corrupt - BLOCKER
- `ARC003`: archive is encrypted - BLOCKER
- `ARC004`: nested content directory - SAFE_FIX
- `ARC005`: ComicInfo.xml missing from root - SAFE_FIX
- `ARC006`: multiple ComicInfo.xml files - REVIEW
- `ARC007`: decompression bomb risk - BLOCKER
- `ARC008`: path traversal or unsafe entry - BLOCKER
- `ARC009`: unrelated/system files - SAFE_FIX
- `ARC010`: metadata refresh or identity normalization changed any non-ComicInfo member byte or archive member order - BLOCKER without exception

## Images and pages

- `PAGE001`: lexicographic ordering problem - SAFE_FIX
- `PAGE002`: duplicate page - REVIEW
- `PAGE003`: missing sequence - WARNING
- `PAGE004`: unreadable image - BLOCKER for that item
- `PAGE005`: EXIF rotation required - SAFE_FIX/REVIEW
- `PAGE006`: probable spread - INFO/REVIEW
- `PAGE007`: cover not identified - WARNING
- `PAGE008`: extreme dimensions or memory risk - WARNING

## PDF/EPUB

- `PDF001`: encrypted or access-restricted PDF - BLOCKER
- `PDF002`: PDF contains text/bookmarks/links that CBZ will not preserve - WARNING
- `PDF003`: PDF page render failed - BLOCKER for affected page/item
- `PDF004`: page count mismatch - BLOCKER
- `PDF005`: multi-chapter volume boundary is ambiguous - WARNING with documented automatic volume fallback when volume identity, integrity, and exact full-page coverage are confirmed; otherwise REVIEW
- `PDF006`: selected chapter-split spans are discontinuous, overlapping, or do not cover every source page exactly once - BLOCKER for chapter splitting; use the verified volume fallback when eligible, otherwise REVIEW/BLOCKER
- `EPUB001`: reflowable text EPUB - PRESERVE, do not auto-convert
- `EPUB002`: broken spine or missing resource - BLOCKER
- `EPUB003`: DRM/encryption detected - BLOCKER

## Metadata

- `META001`: no Bangumi candidate - REVIEW
- `META002`: multiple close Bangumi candidates - REVIEW
- `META003`: Bangumi match below threshold - REVIEW
- `META004`: locked user field would be overwritten - BLOCKER for overwrite
- `META005`: invalid ComicInfo.xml - SAFE_FIX/REVIEW
- `META006`: language or direction uncertain - REVIEW
- `META007`: alternate edition duplicates a primary Kavita chapter identity - REVIEW
- `META008`: a Chinese-required non-Summary field lacks a reliable Chinese value or would require Japanese fallback - REVIEW
- `META009`: Publisher bilingual components are incomplete, unreliable, or incorrectly formatted - REVIEW
- `META010`: tag contains unapproved Han text outside recognized release tags/allowlist or lacks a reliable canonical value - REVIEW
- `META011`: protected `cosplay` or a user-locked allowlist tag was lost or changed - BLOCKER
- `META012`: Japanese Summary translation is unreliable, unnatural, contains unsupported additions or unresolved names, or retains Japanese display text - REVIEW

## Specials, resume, and promotion

- `SPECIAL_RANGE_INCOMPLETE`: contents lists a Special but no complete independent page range exists - REVIEW; create nothing
- `SPECIAL_DUPLICATE_PAGE`: a companion page duplicates retained content by byte/perceptual evidence - INFO/REVIEW
- `RESUME_SOURCE_CHANGED`: a source hash changed since checkpoint - BLOCKER for deterministic resume
- `RESUME_BASELINE_CHANGED`: the formal-library baseline changed since checkpoint - BLOCKER for promotion
- `PROMOTION_POSTCHECK_FAILED`: validation from the promoted formal path failed - BLOCKER; roll back
