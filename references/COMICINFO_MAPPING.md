# ComicInfo.xml mapping guide

Use the current Anansi ComicInfo schema supported by the target reader. Preserve unknown fields when updating existing XML.

## Identity

| Purpose | ComicInfo field |
|---|---|
| Book/chapter title | `Title` |
| Canonical series | `Series` |
| Chinese/localized series | `LocalizedSeries` |
| Sort name | `SeriesSort` |
| Chapter/issue | `Number` |
| Volume | `Volume` |
| Total issue count | `Count` |

## Description and publication

| Purpose | ComicInfo field |
|---|---|
| Chinese description | `Summary` |
| Notes/provenance | `Notes` |
| Release date | `Year`, `Month`, `Day` |
| Publisher | `Publisher` |
| Imprint | `Imprint` |
| Reference page | `Web` |
| ISBN/EAN/JAN | `GTIN` |

## People

Use evidence-based role mapping:

- `Writer`
- `Penciller`
- `Inker`
- `Colorist`
- `Letterer`
- `CoverArtist`
- `Editor`
- `Translator`

Do not map every Bangumi person to `Writer`. Preserve the original role in the report when mapping is uncertain.

## Classification

- `Genre`: reliable Chinese broad genres, comma-separated.
- `Tags`: validated canonical tags plus recognized Chinese release-tag exceptions, comma-separated; see `METADATA_POLICY.md`.
- `Format`: `Digital`, `Web`, `Artbook`, `Special`, or another meaningful value.
- `AgeRating`: only when the source provides reliable evidence.
- `CommunityRating`: ComicInfo uses a 0-5 range; normalize external ratings carefully and record the conversion.

## Language and direction

- Simplified Chinese: `zh-Hans`
- Traditional Chinese: `zh-Hant`
- Japanese: `ja`
- English: `en`
- Japanese/right-to-left manga: `Manga = YesAndRightToLeft`

## Field language policy

- Read `METADATA_POLICY.md` before creating or updating ComicInfo.
- Require Chinese in `Series`, `LocalizedSeries`, `SeriesSort`, `Title`, `Summary`, and `Genre`; translate only an eligible Japanese `Summary` under `METADATA_POLICY.md`, then validate fidelity, naturalness, and absence of Japanese kana.
- Format `Publisher` as `中文译名（原文名）` when both reliable, different values exist; an original Japanese name in parentheses is allowed.
- Validate `Tags` as non-Chinese canonical terms by default, preserve exact `cosplay`, and allow recognized Chinese release tags plus documented user-locked exceptions.
- Creator-role fields may use an official Japanese name when the person and role mapping is high-confidence and no reliable Chinese form exists.
- Keep `LanguageISO` aligned with the manga edition's content language; it does not authorize Japanese metadata fallback.

## Pages

- `PageCount` must equal the actual packaged image count.
- Use `Pages/ComicPageInfo` to mark `FrontCover`, `Story`, `Advertisement`, `BackCover`, and double-page spreads when known.
- Preserve repeated credit, release, and advertisement pages; mark a page `Advertisement` only when that classification is reliable.
- Page image indices are zero-based under common ComicInfo conventions; verify against the current schema and target reader before writing page entries.

## Series identity mapping

- `continuous-chapter`: set actual `Number`; omit `Volume`; store confirmed source volume in `Notes` and provenance reports.
- `volume-aware-chapter`: set actual `Number` and confirmed `Volume`; use only after explicit user selection.
- `volume-only` or documented fallback: omit `Number`; set confirmed `Volume`; use a reliable Chinese volume `Title`; never mark it `Special`.
- Special: use `Number=SPxx`, `Format=Special`, reliable Chinese `Title`, and the main series' `Series`, `LocalizedSeries`, and `SeriesSort`.
- Set `Count` only from reliable series-level total chapter evidence. Never use it to hide a real gap.
- Never give an alternate edition the same formal-library identity as the selected primary copy.

## Existing-library updates

For `metadata-refresh`, preserve filename, directory, `Series`, `LocalizedSeries`, `SeriesSort`, `Volume`, `Number`, and `Format`; update only non-identity metadata.

For explicitly authorized `identity-normalization`, change only planned identity fields and `Title`. Preserve unknown XML fields unless they conflict with the confirmed new identity. Preserve every non-ComicInfo member byte and archive member order, and write before/after identity plus member hashes to the required audit.

Reject `DOCTYPE` or entity declarations before parsing. Keep exactly one UTF-8 `ComicInfo.xml` at archive root and never resolve external resources.
