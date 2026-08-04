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

Japanese/original titles are matching evidence only. Japanese prose may also serve as the source for the controlled `Summary` translation in `METADATA_POLICY.md`. Verified creator names follow the explicit exception below.

## Field application

- Default to simplified Chinese (`zh-Hans`); use traditional Chinese (`zh-Hant`) when configured.
- Prefer the current API's Chinese-localized field such as `name_cn`, followed by a verified Chinese alias or existing locked Chinese metadata. Treat the original `name`, kana, and Japanese aliases as match/provenance data only.
- Require Chinese in `Series`, `LocalizedSeries`, `SeriesSort`, `Title`, `Summary`, and `Genre`; when no reliable Chinese `Summary` exists, translate a reliable Japanese summary into natural Chinese under `METADATA_POLICY.md`.
- Allow official Japanese creator names in evidence-backed role fields such as `Writer`, `Penciller`, and `CoverArtist` when no reliable Chinese form exists; this exception never applies to titles, while `Summary` uses only the controlled translation exception.
- Apply bilingual `Publisher` and `Tags` rules from `METADATA_POLICY.md`, including recognized release-tag exceptions; never copy other Bangumi Chinese tags directly without validation.
- Do not translate titles or other missing display fields. Translate only an eligible Japanese `Summary`; if it cannot pass fidelity and naturalness review, preserve a locked value or send it to review.

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
