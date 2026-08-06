# Resume, pause, and recovery

Long-running work must survive process exit without guessing what completed. Keep control state beside the output in a private persistent sibling staging area, never only in a system temporary directory or inside the source tree.

## Persistent control records

Maintain atomically replaced `_reports/resume-state.json`, `_reports/decision-resolution.csv`, and `_reports/decision-resolution.md`. Before replacing an existing formal library, `_reports/promotion-journal.json` is required: persist promotion intent and expected candidate/formal/backup manifests before the first rename, then checkpoint every observed rename, postcheck, rollback, and restored-manifest verification. Flush decisions before the operation they authorize and checkpoint again at the next safe boundary. Keep credentials, API tokens, and unneeded source content out of state.

Decisions use stable series and item IDs derived from canonical locators plus source fingerprints. Each decision records its type, scope, selected value, evidence, authority, input/config fingerprint, status, and any decision it supersedes. Never replace a decision silently.

OCR permission is a decision, not a runtime flag. Persist whether permission was granted or denied, its exact item/series scope and purpose, the authorizing user action, and revocation or supersession. Missing, expired, mismatched, or ambiguous authorization means OCR is forbidden. A resume must not broaden the original scope.

## Safe pause boundaries

On a pause or interrupt request, set `pause_requested`, finish or roll back the current atomic operation, checkpoint, release locks safely, and then stop. Safe boundaries include completion of one inventory record, one extraction, one rendered source span, one staged package, one validation, or one fully journaled promotion/rollback.

Do not pause while an archive, state file, backup, or destination replacement is partially written. If termination prevents a clean boundary, the journal must make the incomplete operation detectable and recoverable on restart.

## Persistent sibling staging

- Derive the staging locator deterministically from the resolved output and run identity; record it before the first write.
- Keep candidates, manifests, validation results, backups, and journals there until completion and the configured recovery-retention condition is satisfied.
- Restrict permissions, prevent concurrent ownership with a lock carrying the run identity, and treat a stale lock as a recovery case rather than deleting it automatically.
- Verify that staging and destination support the intended atomic promotion. If they do not share the required filesystem semantics, use a journaled copy, flush, independent hash check, and final switch.
- Cleanup is a separate, explicit, audited step. A pause, failure, or successful rollback must not erase recovery evidence.

## Deterministic resume

At restart, load and validate state before scanning for ad hoc files. Accept only the supported schema version and lifecycle states. Verify state checksums, actual tool/config/plan/decision-log files against their fingerprints, resolved source and output identities, input manifest fingerprints, decisions, staged artifact hashes, and journal state. Bind a journal to the same `run_id`, recorded candidate, formal path, and state-selected journal path; never reconcile a valid journal from another run. A terminal `validated-final` state is invalid unless its bound journal is also `validated-final`, its formal manifest still equals the promoted candidate, and its verified backup and successful formal-path postcheck remain present.

Reuse completed work only when its dependencies and expected hashes still match. Invalidate the smallest affected downstream stages when an input, decision, configuration, or tool contract changed; regenerate the plan before continuing. Never replay a completed promotion solely because its in-memory acknowledgement was lost—reconcile the journal, destination, candidate, and backup hashes first.

Process ready items in the persisted deterministic order. Retry counts and error classifications are state, not ordering inputs. Items awaiting review remain isolated while independent ready items continue.

## Promotion and rollback protocol

For every destination mutation:

1. Reject symlinks or special filesystem nodes anywhere in the staged tree, validate the candidate, and record its expected manifest and hash. Partition every pre-promotion formal archive exactly once into `affected_formal_archives` or `unaffected_archive_hashes`; an unaffected archive must have the same path and whole-archive SHA-256 in the candidate.
2. If a destination exists, create and verify a recoverable backup; record its manifest and hash.
3. Write and flush a promotion intent containing candidate, destination, backup, and expected hashes.
4. Promote atomically when supported; otherwise use the verified journaled switch described above.
5. Record observed destination state, then postcheck from the formal path: every `checksums.sha256` entry, CBZ count, total pages, ZIP CRC, root ComicInfo parsing, `PageCount`, image decoding, series identity uniqueness, source/review-copy hashes, unaffected hashes, and backup existence.
6. Mark complete only after postchecks pass. Preserve the backup until the retention rule permits cleanup.
7. On failed or indeterminate postcheck, mark rollback pending, restore the verified backup or remove only a newly created destination, verify the restored state, and record rollback completion. Keep the failed candidate and evidence for diagnosis.

Recovery reconciles these durable facts rather than trusting a status label. Use `scripts/library_state.py recover-promotion` first in dry-run mode; it compares the journal's candidate, formal, and backup manifests and may execute only a uniquely proven continue, postcheck-finalize, no-op close, or rollback-complete action. Ambiguous observations remain a blocker with every artifact intact.

## Required resume-state fields

State must include:

- schema version, run ID, lifecycle status, current stage, checkpoint sequence, and state checksum;
- resolved source directory, formal output directory, backup directory, persistent staging locator, lock owner, and filesystem capability result;
- selected profile and identity policy, tool versions, configuration hash, and plan hash;
- absolute or safely resolvable configuration, plan, and decision-log paths whose SHA-256 values match those hashes;
- input manifest with stable item IDs, canonical locators, type, size, modification fingerprint, and source-file hashes;
- formal-library baseline hashes, the exact `affected_formal_archives`/`unaffected_archive_hashes` partition, completed archive list, pending archive list, last completed atomic unit, per-item dependencies/order/status, and current candidate-library state;
- expected and observed artifact locators, manifests, hashes, validation results, and source-immutability checks;
- confirmed chapter boundaries, decision-log digest, scoped OCR authorization and visual-review conclusions, primary selection, user-ignored damaged items, Special dedupe conclusions, and locked metadata;
- pause request state and the operation that must reach an atomic boundary;
- backup locator/hash/verification, promotion intent and journal offset, destination observations, postcheck results, rollback status, and recovery-retention status.

Terminal success requires every promoted artifact to pass postchecks, every source immutability check to pass, no promotion or rollback to remain indeterminate, and the final report to reference the durable state and decision digests.
