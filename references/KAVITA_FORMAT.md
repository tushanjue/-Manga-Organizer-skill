# Kavita output rules used by this skill

## Core layout

For a Kavita library profile, media files must not be placed directly at the library root. Each series must be nested in its own directory, and the same series should not be split across adjacent series folders.

Recommended volume layout:

```text
Library Root/
└── Series Name/
    ├── Series Name v01.cbz
    ├── Series Name v02.cbz
    └── Specials/
        └── Series Name SP01 Artbook.cbz
```

Recommended chapter layout:

```text
Library Root/
└── Series Name/
    ├── Series Name Vol.01 Ch.001.cbz
    ├── Series Name Vol.01 Ch.002.cbz
    └── Series Name Vol.02 Ch.010.5.cbz
```

Kavita uses filenames and internal metadata, not folder hierarchy alone, to parse series, volume, chapter, and special status.

## CBZ internals

A normalized CBZ should look like:

```text
Series Name v01.cbz
├── 0001.jpg
├── 0002.jpg
├── 0003.webp
└── ComicInfo.xml
```

`ComicInfo.xml` must use that exact capitalization and be placed at archive root. Internal metadata can override information parsed from filenames.

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
