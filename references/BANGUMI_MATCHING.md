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

## High-confidence evidence

- exact normalized title or alias;
- subject type consistent with book/manga;
- matching author/artist/publisher;
- compatible publication date;
- exact ISBN/GTIN;
- strong volume-specific evidence;
- clear lead over the second candidate.

## Safe application

Series-level fields may be shared across volumes. Volume-specific cover, date, ISBN, and title should only be applied when the selected subject actually represents that volume.

Store the selected Bangumi subject ID and canonical reference in the report. Put the reference into `Web` only when it is a stable public subject page.
