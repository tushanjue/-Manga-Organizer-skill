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

- `Genre`: broad genres, comma-separated.
- `Tags`: narrower tags, comma-separated.
- `Format`: `Digital`, `Web`, `Artbook`, `Special`, or another meaningful value.
- `AgeRating`: only when the source provides reliable evidence.
- `CommunityRating`: ComicInfo uses a 0-5 range; normalize external ratings carefully and record the conversion.

## Language and direction

- Simplified Chinese: `zh-Hans`
- Traditional Chinese: `zh-Hant`
- Japanese: `ja`
- English: `en`
- Japanese/right-to-left manga: `Manga = YesAndRightToLeft`

## Pages

- `PageCount` must equal the actual packaged image count.
- Use `Pages/ComicPageInfo` to mark `FrontCover`, `Story`, `Advertisement`, `BackCover`, and double-page spreads when known.
- Page image indices are zero-based under common ComicInfo conventions; verify against the current schema and target reader before writing page entries.
