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
