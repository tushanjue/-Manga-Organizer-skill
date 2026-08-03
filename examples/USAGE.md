# Usage examples

## Simplest request

```text
使用 manga-organizer 整理这个文件夹：
/Volumes/漫画/待整理
```

The skill will use the default Kavita-by-volume profile, preserve source files, perform preflight, fetch high-confidence Bangumi metadata, write ComicInfo.xml, and validate the output.

## Specify destination

```text
整理 /Users/me/Downloads/漫画，输出到 /Volumes/NAS/Kavita/漫画。
先显示问题报告；无阻断问题时自动继续。
```

## Chapter-based library

```text
把 D:\MangaInbox 按 Kavita 章节模式整理，简体中文，日漫从右向左。
```

## No network metadata

```text
整理 ~/漫画/待处理，只使用现有 ComicInfo.xml 和文件名，不访问 Bangumi。
```

## Custom profile

```text
使用配置文件 /path/to/manga-organizer.config.yaml 整理 /path/to/source。
```

## Review-only

```text
只检查 /Volumes/漫画/待整理，不修改和转换任何文件，输出完整计划。
```
