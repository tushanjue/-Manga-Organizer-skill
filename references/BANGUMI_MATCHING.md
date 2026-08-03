# Bangumi matching rules

1. Use the current official public API under `api.bgm.tv` and verify endpoint schemas at runtime.
2. Use a compliant custom User-Agent with developer identity, application/skill name, and version.
3. Prefer public API calls; use an access token only for endpoints that require it.
4. Cache responses and use retry with exponential backoff.
5. Never use webpage scraping as the primary metadata path.

## Search sequence

Try, in order:

1. exact cleaned Chinese title;
2. traditional/simplified variant;
3. Japanese title;
4. aliases parsed from existing metadata;
5. ISBN/GTIN;
6. manually supplied subject ID.

Japanese/original titles and prose are matching evidence only; they are not valid fallbacks for Chinese-required fields. Verified creator names follow the explicit exception below.

## Mandatory Chinese output

- Default to simplified Chinese (`zh-Hans`); use traditional Chinese (`zh-Hant`) when configured.
- Prefer the current API's Chinese-localized field such as `name_cn`, followed by a verified Chinese alias or existing locked Chinese metadata. Treat the original `name`, kana, and Japanese aliases as match/provenance data only.
- Require Chinese display values for Bangumi-derived `Series`, `LocalizedSeries`, `SeriesSort`, `Title`, `Summary`, `Genre`, `Tags`, and publisher names.
- Allow official Japanese creator names in evidence-backed role fields such as `Writer`, `Penciller`, and `CoverArtist` when no reliable Chinese form exists; this exception never applies to titles or summaries.
- Reject Japanese fallback text containing hiragana or katakana. Accept Han-only names as Chinese output only when they come from a Chinese-designated field or another verified Chinese source.
- If no reliable Chinese value exists for a Chinese-required field, leave it unchanged or pending, add `META008`, and route it to `_Needs Review`; never copy Japanese into titles or summaries.
- Do not machine-translate or invent missing Chinese metadata unless the user explicitly permits translation. Except for verified creator names, keep original Japanese values only in reports as provenance.

## High-confidence evidence

- exact normalized title or alias;
- subject type consistent with book/manga;
- matching author/artist/publisher;
- compatible publication date;
- exact ISBN/GTIN;
- strong volume-specific evidence;
- clear lead over the second candidate.

## Safe application

Series-level fields may be shared across volumes. Volume-specific cover, date, ISBN, and Chinese title should only be applied when the selected subject actually represents that volume.

Store the selected Bangumi subject ID and canonical reference in the report. Put the reference into `Web` only when it is a stable public subject page.
