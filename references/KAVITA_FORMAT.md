# Kavita output rules used by this skill

## Core layout

For a Kavita library profile, media files must not be placed directly at the library root. Each series must be nested in its own directory, and the same series should not be split across adjacent series folders.

Default chapter layout:

```text
Library Root/
└── Series Name/
    ├── Series Name Vol.01 Ch.001.cbz
    ├── Series Name Vol.01 Ch.002.cbz
    ├── Series Name Ch.003.cbz
    └── Specials/
        └── Series Name SP01 Artbook.cbz
```

Use one chapter per CBZ by default. Use the volume layout only when the user explicitly asks for one CBZ per volume:

```text
Library Root/
└── Series Name/
    ├── Series Name v01.cbz
    └── Series Name v02.cbz
```

Kavita uses filenames and internal metadata, not folder hierarchy alone, to parse series, volume, chapter, and special status.

## CBZ internals

A normalized chapter CBZ should look like:

```text
Series Name Vol.01 Ch.001.cbz
├── 0001.jpg
├── 0002.jpg
├── 0003.webp
└── ComicInfo.xml
```

`ComicInfo.xml` must use that exact capitalization and be placed at archive root. Internal metadata can override information parsed from filenames.

## Chapter identity and filenames

- Known volume: `<Series> Vol.{volume:02} Ch.{chapter:03}.cbz`
- Unknown volume: `<Series> Ch.{chapter:03}.cbz`
- Normal chapters must have unique Kavita `Series`/`Volume`/`Number` identities.
- Put alternate language, raw, or other editions of the same chapter in `_Needs Review/Alternate Editions` unless one can be selected as the high-confidence primary edition. Record why; never overwrite or discard alternates.
- Put confirmed extras, appendices, artbooks, and setting material in `Specials` with an `SP` number and `Format=Special`.

## Multi-chapter volume PDFs

Never package a multi-chapter volume PDF as one CBZ under the default profile. Before conversion, identify the volume's chapter range and produce a split table containing source file, volume, chapter, inclusive start/end pages, packaged page count, boundary evidence, confidence, and front/end matter, credits, or release-page flags.

Use boundary evidence in this order:

1. PDF bookmarks, table of contents, and embedded metadata;
2. existing filenames, folder names, and ComicInfo;
3. reliable volume-to-chapter mappings and public chapter page counts;
4. local structural evidence such as page-size changes, chapter title pages, and repeated release pages;
5. OCR only with the user's explicit permission.

Require continuous numbered chapters, contiguous page spans, no overlaps, and `sum(end - start + 1) == source PDF page count`. Assign every source page exactly once. If any condition fails, do not guess: move the volume to `_Needs Review` and record the failure in `_reports/chapter-boundaries.json`.

Assign front cover, contents, and other front matter to the volume's first chapter. Assign end matter, credits, release pages, and advertisements to the last chapter. Keep repeated credit, release, and advertisement pages; mark reliably identified advertisement pages as `Advertisement` in ComicInfo `Pages`. Split out a `Specials` item only when it is a high-confidence independent extra.

Inputs already representing one chapter per PDF, image-based EPUB, or archive remain one-to-one chapter CBZs.

## Chapter ComicInfo

Set `Series`, `LocalizedSeries`, and `SeriesSort` consistently; `Number` to the actual chapter; `Volume` only to a confirmed volume; `Count` only from reliable total-chapter evidence; and `PageCount` to the actual packaged image count. Retain evidence-backed `LanguageISO`, `Manga`, and other metadata.

## Common volume markers

Examples recognized by common Kavita naming rules include:

```text
v1
vol 1
vol. 1
volume 01
第01卷
卷2
册2
2巻
```

For specials, use an `SP` marker or appropriate `Format` metadata.

## Runtime verification

Because Kavita changes over time, the agent must confirm current official scanner and metadata documentation before relying on edge-case parsing behavior.
