# Usage examples

## Simplest request

```text
使用 manga-organizer 整理这个文件夹：
/Volumes/漫画/待整理
```

The skill will use the default Kavita-by-chapter profile, produce one CBZ per chapter when boundaries are reliable, automatically fall back to one CBZ per confirmed volume when they are not, preserve source files, perform preflight, require Chinese Bangumi titles and summaries without Japanese fallback, preserve verified creator names, write ComicInfo.xml, and validate the output.

## Specify destination

```text
整理 /Users/me/Downloads/漫画，输出到 /Volumes/NAS/Kavita/漫画。
先显示问题报告；无阻断问题时自动继续。
```

## Multi-chapter volume PDFs

```text
把 D:\MangaInbox 中的卷级 PDF 优先按话拆分，每话一个 CBZ。先显示章节拆分表；没有可靠话级边界但卷号明确时，自动按卷生成一个 CBZ 并报告回退原因。
```

## Explicit volume-based library

```text
我明确需要每卷一个 CBZ：把 D:\MangaInbox 按 kavita-volume 整理。
```

## No network metadata

```text
整理 ~/漫画/待处理，只使用现有 ComicInfo.xml 和文件名，不访问 Bangumi。
```

## Metadata-only library update

```text
只更新现有 CBZ 的 ComicInfo.xml：系列名和话名使用中文，Publisher 使用“史克威尔艾尼克斯（スクウェア・エニックス）”，保留 cosplay，不改动图片、页序或章节编号。
```

## Custom profile

```text
使用配置文件 /path/to/manga-organizer.config.yaml 整理 /path/to/source。
```

## Review-only

```text
只检查 /Volumes/漫画/待整理，不修改和转换任何文件，输出完整计划。
```
