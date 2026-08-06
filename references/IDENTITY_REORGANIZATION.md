# Series identity and reorganization

Use one recorded identity policy for every normal package in a series. A package must not silently switch policy because its source naming differs. Specials are outside the normal chapter or volume sequence.

## Series-level policies

`continuous-chapter` is the default. Normal chapters use one series-wide chapter namespace and a canonical key of `(series, chapter)`. Source volume labels must not reset chapter numbering or create a second identity. Keep confirmed source-volume information as provenance; expose it in display metadata only when it cannot change scanner identity.

`volume-aware-chapter` is allowed only after an explicit user or locked-configuration decision. Chapters may restart within each confirmed volume and use `(series, volume, chapter)` as the canonical key. Every normal package must have a confirmed volume and chapter; missing or ambiguous components go to review. Persist the authorization and the old-to-new identity mapping.

`volume-only` packages the whole series by complete confirmed volumes under `(series, volume)` and omits chapter `Number`. Do not fabricate chapter identities or mix chapter packages into a `volume-only` series. Separately, `continuous-chapter` may coexist with individually verified, documented fallback volumes; those fallbacks use `(series, volume)`, omit `Number`, and never make normal chapters carry `Volume`.

Changing a series policy is identity normalization, not metadata cleanup. Preview and audit the whole series before writing anything.

## Provenance and gaps

- Record each package's source item ID, source hash, observed series/volume/chapter labels, embedded identity, and the evidence used to choose its canonical identity.
- Preserve `source_volume` even when the selected policy omits or transforms output `Volume`. Provenance must never be reconstructed from the normalized filename.
- Audit gaps in the namespace defined by the selected policy. Never close a gap by renumbering automatically.
- A gap is intentional only when a persisted decision identifies its scope, evidence, authority, and reason. Otherwise classify it for review. Intentional gaps remain visible in reports and do not count as validation failures.
- A special, alternate edition, withheld item, or volume fallback does not by itself prove an intentional gap. An absent source volume with unknown chapter mapping is `unmapped_source_absence`, with `chapter_range: null`; never guess the missing chapters.
- A fallback may mark a numbered range as `fallback_covered_range` only from reliable volume-to-chapter evidence and after cross-package overlap audit. Without that mapping, record `coverage_relation: unnumbered-volume-coverage`; the fallback remains valid but does not silently close a chapter gap.

## Metadata refresh versus identity normalization

A metadata refresh may update descriptive fields and root `ComicInfo.xml` only. It must preserve policy, canonical key, filename identity tokens, package boundaries, primary selection, page/member order, and all non-metadata bytes.

Identity normalization may change policy, keys, filenames, package boundaries, or primary selection only when explicitly requested. It requires a complete before/after mapping, collision and gap audits, persistent decisions, backups, staging, validation, atomic promotion, and rollback support. Never describe identity-changing work as metadata-only.

## Primaries and alternates

- Elect one primary per canonical key using recorded evidence and deterministic tie-breaking. Persist its item ID, content hash, score inputs, and selection reason.
- Persist every alternate with its relationship to the primary, reason, identity evidence, and hash. Never overwrite, discard, or silently relabel it to avoid a collision.
- A resume or metadata refresh reuses the persisted election. Re-election requires explicit identity normalization or evidence that the stored primary is unavailable or invalid, followed by a new audit decision.
- Keep alternates outside the active library identity namespace, while retaining stable links from the audit record to their staged or review artifacts.

## Specials, merging, and deduplication

- Give each confirmed special a stable special key and `Format=Special`; it must not occupy or repair a normal sequence position.
- Merge official/high-confidence companion material into the main series `Specials` only when the user requests it or `companion_material_policy=merge-specials`; never create a second formal series in that mode. Route unofficial or uncertain doujin material to review.
- Exact byte duplicates may share one active artifact only after hashes and decoded-page counts agree. Use perceptual hashes to find candidates, then visually review non-exact matches before omission. Bind every kept output page to the actual preserved source archive/page SHA-256. Bind every omitted page to its actual source bytes, retained target page, evidence, positive hash-bound visual decision when non-exact, and a verified review-copy hash. Retain every source occurrence in provenance.
- Matching titles, page counts, or perceptual hashes alone are insufficient for automatic deletion.
- Merge only complete, unique sources/ranges proven to belong together, with deterministic order, no conflicting spans, and a persisted decision. Record every component hash/span and every omitted source page, duplicate target, evidence, and preservation status.
- Different languages, scans, revisions, censorship states, translations, or release groups remain alternate editions unless the user explicitly requests a verified merge.

## Stable identity audit codes

- `SERIES_IDENTITY_MIX` — one series mixes incompatible normal-package policies: **BLOCKER**.
- `PARTIAL_VOLUME_TAGGING` — only some normal chapters carry `Volume`: **BLOCKER**.
- `KAVITA_VOLUME_JUMP_RISK` — source-volume identity can cause scanner jumps: **REVIEW/BLOCKER**.
- `DUPLICATE_CHAPTER_IDENTITY` — two active items resolve to one canonical chapter: **BLOCKER**.
- `UNDOCUMENTED_VOLUME_FALLBACK` — a fallback lacks eligibility evidence or reports: **BLOCKER**.
- Use `UNINTENDED_GAP`, `CONFIRMED_SOURCE_GAP`, and `USER_IGNORED_DAMAGED_ITEM` from `ISSUE_CODES.md` for sequence classification.

## Required audit output

For each series, write the selected policy/authority, normal-chapter count, Special count, fallback count, chapters carrying Volume, duplicate identities, unintended gaps, confirmed source gaps, policy history, source-to-canonical mapping, primary/alternate registry, and Special dedupe records. Include before/after filenames and ComicInfo identity fields plus artifact/member hashes for normalization. Completion requires no unresolved identity blocker.

Identity normalization must write `_reports/series-identity-audit.csv`, `_reports/identity-normalization-member-integrity.csv`, and `_reports/reorganization-YYYYMMDD.json`, deriving `YYYYMMDD` at runtime. Every changed archive/member row must include `old_output`, `new_output`, `identity_before`, `identity_after`, `changed_fields`, `change_reason`, `member_index`, `member_name`, `is_metadata`, `before_sha256`, `after_sha256`, `content_unchanged`, and `member_order_unchanged`. Before promotion, partition every old formal archive into the explicit affected set or the path-to-hash unaffected set; the sets must be disjoint and exhaustive. Record counts for both and verify every unaffected path and whole-archive SHA-256 in the candidate.
