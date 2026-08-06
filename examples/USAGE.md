# Usage examples

## New continuous-chapter library

```text
使用 $manga-organizer 整理这个文件夹，按 continuous-chapter 输出；正常章节统一为 Ch.xxx，不把来源卷号写入 Kavita Volume。
```

## Verified volume fallback

```text
优先按章节拆分；完整且卷号明确但找不到可靠边界的卷按 vXX.cbz 收录，列出尝试过的证据和全页覆盖结果。
```

## Repair Kavita volume jumps

```text
对现有 Manga Organizer Output 做 identity-normalization：清除正常章节中造成跳卷的部分 Volume 身份，保留 Number 和来源卷号审计；图片及非 XML 成员不得改变。
```

## Merge official companion material

```text
把高置信官方选集并入主系列 Specials，先做字节哈希、感知哈希和视觉去重；保留所有唯一页面并报告未收录页面。
```

## Preserve real gaps and ignored damage

```text
缺少源文件的章节保持空缺，不重编号也不生成占位；我确认忽略的损坏项目保留源文件和审阅副本，不阻塞其他内容。
```

## Pause and resume

```text
完成当前归档后暂停并写入持久 resume-state.json；下次从最后完成单元继续，不重复询问已确认的 OCR 权限、边界和主版本。
```

## Metadata refresh only

```text
只做 metadata-refresh：不得改文件名、目录或 Series/Volume/Number/Format，不得改变图片、其他成员或成员顺序。
```

## Review without mutation

```text
只做预检和系列身份审计，输出计划、真实缺章、回退卷和待解决决定，不创建或修改任何漫画归档。
```
