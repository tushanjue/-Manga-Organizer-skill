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

Prefer one chapter per CBZ. Use the volume layout when the user explicitly asks for one CBZ per volume or when an individual confirmed volume has no reliable chapter boundaries:

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

First attempt chapter-level packaging. Before conversion, identify the volume's chapter range and produce a split table containing source file, volume, chapter, packaging mode, inclusive start/end pages, packaged page count, boundary evidence, confidence, fallback reason, and front/end matter, credits, or release-page flags.

Use boundary evidence in this order:

1. PDF bookmarks, table of contents, and embedded metadata;
2. existing filenames, folder names, and ComicInfo;
3. reliable volume-to-chapter mappings and public chapter page counts;
4. local structural evidence such as page-size changes, chapter title pages, and repeated release pages;
5. OCR only with the user's explicit permission.

When reliable boundaries exist, require continuous numbered chapters, contiguous page spans, no overlaps, and `sum(end - start + 1) == source PDF page count`. Assign every source page exactly once.

If reliable chapter boundaries remain unavailable after checking allowed evidence, automatically fall back to one volume CBZ only when all of these conditions hold:

1. the file is high-confidence evidence for exactly one series and one confirmed volume;
2. the volume number is known and there is no evidence that the file mixes volumes;
3. every source page is readable and the full `1..N` range can be packaged exactly once in natural order;
4. the archive passes the normal integrity, image, ComicInfo, and checksum validations.

Name the fallback `<Series> v{volume:02}.cbz`. In ComicInfo, set the confirmed `Volume`, omit `Number`, use a reliable Chinese volume title such as `第{volume}卷`, and set the actual `PageCount`. Do not invent chapter ranges or numbers. In `_reports/chapter-boundaries.json`, set `packaging_mode` to `volume-fallback`, set chapter to null, record attempted evidence and the fallback reason, and verify the single `1..N` span equals the source page count.

If the volume identity, file integrity, or full-page coverage is uncertain, move the item to `_Needs Review`; automatic fallback does not authorize guessing those facts.

When splitting, assign front cover, contents, and other front matter to the volume's first chapter. Assign end matter, credits, release pages, and advertisements to the last chapter. In volume fallback mode, retain all pages once in source order. Keep repeated credit, release, and advertisement pages; mark reliably identified advertisement pages as `Advertisement` in ComicInfo `Pages`. Split out a `Specials` item only when it is a high-confidence independent extra.

Inputs already representing one chapter per PDF, image-based EPUB, or archive remain one-to-one chapter CBZs.

## Existing library updates

For metadata-only updates, keep the existing CBZ's images, other non-metadata members, member order, page order, and chapter identity unchanged. Create a backup, build and validate the candidate in staging, compare per-member hashes, then atomically replace the output. See `METADATA_POLICY.md`.

## Package ComicInfo

Set `Series`, `LocalizedSeries`, and `SeriesSort` consistently; `Number` to the actual chapter; `Volume` only to a confirmed volume; `Count` only from reliable total-chapter evidence; and `PageCount` to the actual packaged image count. Retain evidence-backed `LanguageISO`, `Manga`, and other metadata.

For a volume fallback, omit `Number`, set the confirmed `Volume`, use a reliable Chinese volume title, and keep the full source page count. Do not mark a fallback volume as `Special`.

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
