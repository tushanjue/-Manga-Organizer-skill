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

## Chapter packaging

- For normal chapter CBZs, set `Number` to the actual chapter and `Volume` only when confirmed.
- For an automatic volume fallback, omit `Number`, set the confirmed `Volume`, use a reliable Chinese volume `Title` such as `第{volume}卷`, and set `PageCount` to the complete packaged image count; never fabricate chapter identity or mark the volume as `Special`.
- Set `Count` to the series' total chapter count only when supported by reliable evidence.
- Keep `Series`, `LocalizedSeries`, and `SeriesSort` consistent across the primary edition.
- Give confirmed extras, appendices, and setting material an `SP` number and `Format=Special`; do not assign them a normal chapter identity.
- Do not give alternate editions the same Kavita `Series`/`Volume`/`Number` identity as the selected primary library copy.
