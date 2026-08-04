# ComicInfo metadata policy

Read this file before creating or updating ComicInfo for a Chinese library. Apply the policy after merging all metadata sources.

## Source precedence

```text
locked user value
> existing valid ComicInfo.xml
> exact volume-level metadata
> series-level metadata
> embedded metadata
> filename/folder inference
```
Never overwrite a locked value. This precedence selects candidates; every final `Tags` value must still pass tag validation. Han tags are allowed only when they are recognized release tags or exact user-locked special allowlist entries.

## Field language matrix

| Field | Required policy |
|---|---|
| `Series`, `LocalizedSeries`, `SeriesSort` | Use the same reliable Chinese series name. |
| `Title` | Use a reliable Chinese title. For normal chapters, default to `第{chapter}话`; use reliable Chinese names such as `番外篇` or `特别篇` for specials. |
| `Summary` | Prefer reliable Chinese text; otherwise translate a reliable Japanese source under the policy below. |
| `Genre` | Use reliable Chinese text; never fall back to Japanese or invent a translation. |
| `Publisher` | Use the bilingual policy below. |
| `Tags` | Use canonical non-Chinese terms by default, with recognized release-tag and locked-allowlist exceptions. |
| Creator fields | Reliable official original-language names are allowed. |

Japanese titles are for matching, search, and provenance reports only. Japanese prose may be used only as the source of a controlled `Summary` translation. If another required Chinese value is unavailable, retain a locked Chinese value or leave it pending with `META008`.

## Japanese Summary translation

When no reliable Chinese `Summary` exists and the selected work has a reliable Japanese summary, translate it into Chinese automatically. A per-item user lock that explicitly preserves a value still wins.

1. Confirm the Japanese text belongs to the selected series or volume. Do not translate a low-confidence candidate.
2. Preserve facts, character relationships, chronology, tone, uncertainty, and spoiler level. Do not add explanations, opinions, praise, themes, or promotional claims absent from the source.
3. Rewrite Japanese syntax into concise, idiomatic Chinese suitable for an editor-written manga synopsis. Prefer direct sentences and natural paragraph flow over sentence-by-sentence literal translation.
4. Use reliable existing Chinese names for titles, characters, places, organizations, and terminology. If an essential name has no reliable Chinese form and cannot be rephrased without losing meaning, add `META012` and send the Summary to review rather than inventing a name.
5. Avoid generic AI-style framing and clichés such as `本作讲述了`, `在这个充满……的世界里`, `命运的齿轮开始转动`, `一场……的故事就此展开`, `让我们一起`, `值得一提的是`, or rhetorical questions added by the translator. Do not pad the text with conclusions or moral judgments.
6. Keep Japanese source text out of `Summary`, `Title`, and other Chinese display fields. Record source language, source provenance, translation status, and any unresolved term in the report.
7. Perform a second pass for fidelity and naturalness. Reject output that retains Japanese kana, reads like a literal translation, repeats the same idea, uses unnecessary four-character slogans, or contains information not supported by the source.
If translation or review fails, retain a locked Chinese Summary when available; otherwise leave Summary pending with `META012`. Never silently keep the Japanese synopsis in ComicInfo.

## Publisher

1. Resolve the Chinese translation and original publisher name independently from reliable metadata or high-confidence existing values.
2. When both exist and differ, write `中文译名（原文名）` with fullwidth Chinese parentheses, for example `史克威尔艾尼克斯（スクウェア・エニックス）`.
3. When the original is Chinese, or normalized names are identical, write the reliable value once; do not duplicate it.
4. When either component is missing, preserve the reliable single value, add `META009`, and report the missing component. Do not fabricate a translation or a false bilingual value.
5. An original Japanese name inside the parentheses is an explicit allowed display exception.

## Tags

Treat tags as canonical machine-facing terms, not Chinese display prose.

1. Split and trim tag tokens, preserve their provenance, and record every normalization mapping.
2. Reject a tag containing Unicode Han characters unless it is a recognized release tag or the exact value is in the user-locked special allowlist with a reason and source.
3. Keep reliable English, Latin-script, abbreviations, or widely used original terms. Do not invent English translations for unknown Chinese tags.
4. Never convert `cosplay` to `角色扮演`. If existing ComicInfo contains exact lowercase `cosplay`, the result must retain exact lowercase `cosplay`.
5. Allow recognized release tags such as `简中`, `繁中`, `汉化`, and `扫图` to remain in `Tags`. Also map their meaning to `LanguageISO`, `Translator`, `ScanInformation`, `Notes`, or reports when applicable.
6. Omit or send other uncertain Han tags to review with `META010`; do not keep them merely because they came from Bangumi or existing ComicInfo.

### Canonical vocabulary

This vocabulary normalizes only terms already supported by reliable evidence; it does not authorize inference.

| Canonical tag | Accepted non-Chinese forms | Notes |
|---|---|---|
| `cosplay` | `cosplay`, `Cosplay` | Protected; canonical output is exact lowercase `cosplay`. |
| `4-koma` | `4-koma`, `4koma`, `yonkoma` | Four-panel format. |
| `oneshot` | `oneshot`, `one-shot` | Single self-contained work. |
| `webtoon` | `webtoon` | Widely used original term. |
| `doujinshi` | `doujinshi`, `doujin` | Preserve only when source meaning is reliable. |
| `artbook` | `artbook`, `art-book` | Usually pair with appropriate `Format`. |
| `anthology` | `anthology` | Multi-work collection. |
| `full-color` | `full-color`, `full colour` | Visual format. |
| `monochrome` | `monochrome`, `black-and-white` | Visual format. |
| `isekai` | `isekai` | Widely used original term. |
| `yuri`, `yaoi` | exact token | Widely used original terms. |
| `shounen`, `shoujo`, `seinen`, `josei` | exact token | Demographic terms; do not infer from audience alone. |

### Special allowlist

- Built-in protected tags: `cosplay`.
- Built-in allowed Han release tags: `简中`, `繁中`, `汉化`, `扫图`.
- Configure user exceptions as records containing `value`, `reason`, `source: user`, and `allow_han`.
- Only an exact allowlist entry may bypass normal validation. Record the retained value and reason in the final report.
- If a protected or locked tag is lost or changed, raise `META011` and do not finalize.

## Existing CBZ metadata-only updates

1. Before editing, record chapter identity and each archive member's name, order, uncompressed size, CRC, and SHA-256.
2. Back up the existing CBZ and build the candidate in staging.
3. Change only the root `ComicInfo.xml`; preserve all image and other non-metadata members byte-for-byte and in the same order.
4. Validate metadata, member invariants, ZIP CRC, `PageCount`, page order, chapter sequence, and source hashes before atomic replacement.
5. The ZIP container hash may change because ComicInfo changed; non-metadata per-member hashes must not.

## Required reporting

Report the final Chinese series name, final Publisher, tag normalization mapping, protected/allowlisted tags and reasons, omitted or review-required tags, and the before/after non-metadata member comparison.
