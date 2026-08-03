---
name: manga-organizer
description: Automatically inspect, normalize, enrich, convert, package, and export manga folders for Kavita. Use when the user gives a manga folder or asks to organize loose images, PDF, image-based EPUB, CBZ/ZIP/CBR/RAR/7Z; fetch Chinese metadata from Bangumi; write ComicInfo.xml; and produce a verified, non-destructive output library.
---

# Manga Organizer Skill

Use this skill whenever the user provides a folder path and asks to organize, convert, package, repair, enrich, or export manga.

The goal is to turn a mixed or disordered source folder into a verified manga library without silently damaging the originals.

## 1. Required input and defaults

The only required input is:

- `source`: the exact source folder supplied by the user.

Optional inputs:

- `output`: destination folder.
- `profile`: `kavita-volume`, `kavita-chapter`, `standard-cbz`, `preserve-layout`, or `custom`.
- `language`: `zh-Hans`, `zh-Hant`, `ja`, `en`, or another BCP 47 tag.
- `reading_direction`: `rtl`, `ltr`, or `auto`.
- `metadata`: `bangumi`, `existing-only`, or `none`.
- `mode`: `auto-safe` or `review-all`.
- `config`: path to a custom YAML configuration.

Defaults when the user supplies only a folder:

```yaml
profile: kavita-volume
language: zh-Hans
reading_direction: rtl
metadata: bangumi
mode: auto-safe
preserve_source: true
overwrite_source: false
image_policy: preserve
pdf_profile: balanced-high-quality
epub_policy: convert-image-based-only
```

If `output` is omitted, create a sibling directory rather than a child of the source:

```text
<source-folder-name> - Manga Organizer Output
```

Never place the output inside the source tree because that can cause recursive rescanning.

## 2. Non-negotiable operating rules

1. Treat the source folder as read-only by default.
2. Run a complete preflight before the first write operation.
3. Show the detected problems and proposed output tree before conversion.
4. Never silently overwrite, delete, rename, or move source files.
5. Write to a staging directory, validate, then atomically move into the final destination.
6. Keep backups when updating an existing output file.
7. Do not execute `git init`, `git add`, `git commit`, `git push`, or change Git remotes.
8. Do not claim completion unless generated archives, images, XML, and directory layout have been verified.
9. Continue processing independent high-confidence items even if some items require review; isolate uncertain items instead of failing the entire batch.
10. Never use OCR unless filenames, embedded metadata, and directory context are insufficient and the user explicitly permits it.

## 3. Network and proxy rules

Before any network operation on macOS/Linux, set these environment variables for the current process only:

```bash
export http_proxy="http://127.0.0.1:17891"
export https_proxy="http://127.0.0.1:17891"
export all_proxy="socks5://127.0.0.1:17891"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
```

For PowerShell, use the equivalent session-only variables:

```powershell
$env:http_proxy  = "http://127.0.0.1:17891"
$env:https_proxy = "http://127.0.0.1:17891"
$env:all_proxy   = "socks5://127.0.0.1:17891"
$env:HTTP_PROXY  = $env:http_proxy
$env:HTTPS_PROXY = $env:https_proxy
$env:ALL_PROXY   = $env:all_proxy
```

Do not hard-code this proxy into generated manga files, permanent application settings, or source documents.

Use the current official Bangumi API, not HTML scraping. Before calling it, verify the current API contract and User-Agent guidance. Use a distinct User-Agent containing a developer identifier, skill name, and version. Do not use a library default User-Agent.

## 4. Automatic workflow

Execute the following stages in order.

### Stage A - Resolve and secure the paths

- Confirm the source exists and is readable.
- Resolve symlinks and canonical paths.
- Confirm the output is outside the source.
- Check free disk space and output permissions.
- Detect NAS/SMB paths and use conservative write/retry behavior.
- Create a private staging directory and report directory.

### Stage B - Inventory the source

Recursively inventory supported inputs:

- loose image folders;
- JPG, JPEG, PNG, WebP, GIF, AVIF, BMP, TIFF;
- ZIP, CBZ, RAR, CBR, 7Z, CB7, TAR, CBT;
- PDF;
- EPUB.

Detect actual content using file signatures where practical; do not trust extensions alone.

Record at least:

- path;
- detected type;
- size;
- modified time;
- hash when useful;
- page/image count;
- embedded metadata;
- candidate series, volume, chapter, language, and group tags;
- confidence score;
- detected problems.

### Stage C - Mandatory preflight

Create:

```text
_reports/preflight.md
_reports/plan.json
```

Classify every issue as:

- `BLOCKER`: unsafe or impossible to continue automatically;
- `WARNING`: can continue, but the result may differ from the source;
- `SAFE_FIX`: deterministic and non-destructive repair;
- `INFO`: no action required.

At minimum check:

- files placed directly at a prospective Kavita library root;
- multiple series mixed in one folder;
- one series split between adjacent folders;
- missing or ambiguous series, volume, or chapter numbers;
- conflicting filename and ComicInfo.xml metadata;
- invalid Windows/macOS filename characters;
- nested archives or nested content folders;
- duplicate or malformed ComicInfo.xml files;
- ComicInfo.xml not at archive root;
- `.DS_Store`, `__MACOSX`, `Thumbs.db`, URL files, advertisements, and unrelated documents;
- corrupt, encrypted, empty, or disguised archives;
- path traversal entries, symlinks, decompression bombs, and extreme expansion ratios;
- lexicographic page-order problems such as `1, 10, 2`;
- duplicate pages, missing sequences, unreadable images, zero-byte files, extreme dimensions, and EXIF rotation;
- PDF encryption, page errors, text layer, links, annotations, inconsistent page size, and rotation;
- image-based/fixed-layout versus reflowable EPUB;
- missing language or reading direction;
- low-confidence or multiple Bangumi matches.

Display a concise summary before processing. In `auto-safe` mode:

- proceed automatically only for items with no blockers and no unresolved ambiguity;
- apply deterministic safe fixes;
- place uncertain items in `_Needs Review` with candidates and reasons;
- never guess a destructive or identity-changing decision.

### Stage D - Parse names without losing information

For matching only, derive a cleaned title by removing or separating common release tags such as:

- scanlation group names;
- `简中`, `繁中`, `汉化`, `DL版`, `修正版`, `无修`, `扫图`;
- source website names;
- resolution and image-format labels;
- bracketed release metadata.

Do not discard this information. Preserve relevant values in `Translator`, `ScanInformation`, `Tags`, or the report.

Recognize common numbering forms including:

- `v1`, `vol 01`, `volume 1`, `第01卷`, `卷2`, `册2`, `2巻`;
- `c1`, `ch.001`, `chapter 1`, `第001话`;
- decimal chapters such as `10.5`;
- ranges such as `Vol. 1-5`;
- specials such as `SP01`, `番外`, `特别篇`, `设定集`, and `画集`.

### Stage E - Build a processing plan

Group content into:

```text
Series -> Volume or Chapter -> Ordered Pages
```

Generate a preview showing:

- inferred series and aliases;
- volume/chapter assignment;
- page count and order;
- selected output profile;
- proposed output path;
- metadata source;
- estimated size;
- fixes to be applied;
- items requiring review.

## 5. Conversion rules

### Loose images to CBZ

- Natural-sort pages.
- Normalize archive page names to `0001.ext`, `0002.ext`, and so on.
- Keep original image bytes unless the user asks for image conversion or optimization.
- Apply EXIF orientation without recompressing when possible; otherwise report the required rewrite.
- Put pages directly at the CBZ root.
- Exclude system and unrelated files.
- Support page reorder, rotation, deletion, replacement, cover selection, spread splitting, merge, and split when requested.

### PDF to CBZ

Default policy: preserve quality without unnecessary upscaling.

1. Inspect the PDF first.
2. If each page contains one suitable full-page raster image and extraction preserves the page correctly, prefer lossless extraction.
3. Otherwise render pages with a reliable PDF renderer.
4. For `balanced-high-quality`, use a sensible high-quality default such as 240 DPI and JPEG quality around 92, but do not upscale beyond the useful source resolution.
5. Preserve PNG or another lossless format when transparency or line-art quality requires it.
6. Respect page rotation and crop boxes.
7. Process page-by-page to control memory.
8. Compare PDF page count with output image count.
9. Keep the source PDF.
10. Warn that selectable text, links, bookmarks, forms, annotations, and other PDF-only features may not survive conversion.

### EPUB

- Detect fixed-layout or image-based manga EPUBs.
- Follow the EPUB spine order rather than filename order alone.
- Read and map OPF metadata.
- Convert image-based manga EPUBs to CBZ.
- Do not automatically convert reflowable text EPUBs; retain them under `_Preserved EPUB` and report why.

### Existing archives

- Safely extract CBR/RAR/CB7/7Z/TAR/CBT and repack as CBZ when the selected profile requires CBZ.
- Normalize existing CBZ files only when needed.
- Preserve unknown ComicInfo.xml fields and user metadata.
- Never execute files contained in archives.

## 6. Bangumi metadata workflow

Use Bangumi only after local name parsing.

Search with several normalized candidates:

- simplified Chinese title;
- traditional Chinese title;
- Japanese/original title;
- aliases;
- title with scanlation and release tags removed;
- ISBN or Bangumi subject ID when present.

Use current official endpoints, including subject search and subject detail endpoints as documented at execution time.

Candidate scoring must consider:

- exact title or alias match;
- media type consistent with manga/book;
- author/artist/publisher agreement when available;
- publication date;
- ISBN;
- volume-specific evidence;
- difference between the best and second-best candidate.

Auto-apply only when confidence is high and the winning margin is clear. Recommended default:

```text
auto-match score >= 0.92
and winner margin >= 0.10
```

Otherwise:

- do not overwrite identity fields;
- write candidates to `_reports/bangumi-review.csv`;
- place the item in `_Needs Review` or continue with existing/local metadata, clearly marked as pending.

Metadata merge priority by default:

```text
locked user value
> existing valid ComicInfo.xml
> exact volume-level Bangumi data
> series-level Bangumi data
> EPUB/PDF embedded metadata
> filename and folder inference
```

Use Bangumi series covers for the library record only unless the user explicitly asks to insert or replace a page. Do not silently add a generic series cover as every volume's first page.

Cache successful API responses and continue local processing if the network becomes unavailable.

## 7. ComicInfo.xml rules

Every generated CBZ must contain exactly one UTF-8 `ComicInfo.xml` at the archive root.

Populate fields when evidence exists:

- `Title`
- `Series`
- `LocalizedSeries`
- `SeriesSort`
- `Number`
- `Count`
- `Volume`
- `Summary`
- `Notes`
- `Year`, `Month`, `Day`
- `Writer`
- `Penciller`
- `Inker`
- `Colorist`
- `Letterer`
- `CoverArtist`
- `Editor`
- `Translator`
- `Publisher`
- `Imprint`
- `Genre`
- `Tags`
- `Web`
- `PageCount`
- `LanguageISO`
- `Format`
- `BlackAndWhite`
- `Manga`
- `ScanInformation`
- `AgeRating`
- `CommunityRating`
- `GTIN`
- `Pages` / `ComicPageInfo`

Defaults for translated Japanese manga:

```xml
<LanguageISO>zh-Hans</LanguageISO>
<Manga>YesAndRightToLeft</Manga>
```

Use `zh-Hant` for traditional Chinese. Do not write RTL when the evidence indicates a left-to-right edition.

Set `PageCount` from the actual packaged page count. Mark the selected first cover in `Pages`. Preserve unknown XML elements and extension fields when updating existing metadata. Disable external XML entities and never resolve external resources.

## 8. Output profiles

### `kavita-volume` - default

```text
<output>/
└── <Series>/
    ├── <Series> v01.cbz
    ├── <Series> v02.cbz
    └── Specials/
        └── <Series> SP01 <Title>.cbz
```

### `kavita-chapter`

```text
<output>/
└── <Series>/
    ├── <Series> Vol.01 Ch.001.cbz
    ├── <Series> Vol.01 Ch.002.cbz
    └── <Series> Vol.01 Ch.003.5.cbz
```

### `standard-cbz`

```text
<output>/
├── <Series> v01.cbz
└── <Series> v02.cbz
```

This is portable CBZ packaging, not a complete Kavita library root.

### `preserve-layout`

Keep the user's directory layout and only repair archive structure, page order, and metadata.

### `custom`

Read the user's YAML config. Support variables such as:

```text
{series} {localizedSeries} {title} {volume} {volume:02}
{chapter} {chapter:03} {author} {publisher} {language}
{scanlator} {format} {bangumiId} {page} {page:0000}
```

Validate custom templates for empty values, duplicate names, illegal Windows characters, path length, and Kavita compatibility before writing.

See `templates/manga-organizer.config.yaml`.

## 9. Archive construction and validation

Build CBZ files as ZIP-compatible archives with ZIP64 support when needed.

Before finalizing each archive:

1. confirm pages and `ComicInfo.xml` are at the archive root;
2. verify archive CRC and central directory;
3. reopen the archive using an independent read step;
4. parse `ComicInfo.xml` safely;
5. decode every page image;
6. compare actual pages with `PageCount`;
7. inspect the first, middle, and last page dimensions;
8. confirm natural order;
9. confirm no prohibited or accidental files remain;
10. verify the final filename and path;
11. compute a SHA-256 checksum.

For Kavita output, verify that:

- no media files exist directly at the library root;
- each series is nested in its own folder;
- one series is not split between adjacent folders;
- filenames or internal metadata contain usable volume/chapter information;
- `ComicInfo.xml` is at the CBZ root.

## 10. Reports and final folder

The output must include:

```text
<output>/
├── _reports/
│   ├── preflight.md
│   ├── plan.json
│   ├── execution-report.md
│   ├── bangumi-review.csv
│   ├── skipped-items.csv
│   └── checksums.sha256
├── _Needs Review/
├── _Preserved EPUB/
└── <organized series folders>
```

Omit empty review folders if no items require them.

The final report must state:

- source and output paths;
- selected profile;
- total series, volumes, chapters, pages, and bytes;
- converted PDFs and EPUBs;
- metadata matches and confidence;
- automatic fixes;
- skipped or quarantined items;
- validation results;
- confirmation that source files were not modified;
- any unresolved issues.

## 11. Interaction policy

When the user provides a folder:

1. Start the preflight immediately; do not ask which technology to use.
2. Show the issue summary and proposed structure.
3. If there are no blockers or unresolved identity conflicts, continue automatically in the same workflow.
4. If a decision is genuinely required, ask one consolidated question covering all ambiguous series rather than many small questions.
5. Never ask the user to manually package files that the skill can process itself.
6. Do not stop simply because Bangumi is unavailable; finish safe local work and mark metadata as pending.
7. Do not describe an item as successfully organized until the final archive validation passes.

## 12. Completion checklist

The task is complete only when:

- the source remained unchanged unless the user explicitly authorized otherwise;
- all supported files were inventoried;
- preflight and plan files exist;
- every completed CBZ passes integrity, XML, and image checks;
- PDF page counts match generated pages;
- image-based EPUB order follows the spine;
- Bangumi matches were either high-confidence or left for review;
- ComicInfo.xml contains actual page count and correct language/direction;
- Kavita output passes directory-layout checks;
- checksums and execution report exist;
- no Git operation was performed.

Consult the bundled references for detailed format, metadata, and issue rules.
