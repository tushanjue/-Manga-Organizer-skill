# Manga Organizer Skill

This package is a reusable agent skill for organizing manga folders into verified CBZ/Kavita layouts.

## Install

Copy the entire `manga-organizer-skill` folder into the skills directory used by your coding or file-management agent. Keep `SKILL.md`, `references`, and `templates` together.

The exact skills directory depends on the agent. The skill uses a conventional `SKILL.md` frontmatter format and does not require a specific programming language or framework.

## Invoke

Give the agent a folder path, for example:

```text
使用 manga-organizer 整理 /Volumes/漫画/待整理
```

The default behavior is:

- read-only source;
- mandatory preflight;
- safe automatic fixes;
- PDF and image-based EPUB conversion to CBZ;
- Bangumi metadata matching;
- ComicInfo.xml generation;
- Kavita-by-volume output;
- full validation and reports.

See `examples/USAGE.md` and `templates/manga-organizer.config.yaml` for customization.
