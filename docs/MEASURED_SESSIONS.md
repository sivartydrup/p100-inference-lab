# Multi-GPU measured sessions

The history UI can associate resident measurements from separate GPUs with one operator-declared session. This is read-only history, not browser scheduling or a multi-GPU model split. Each record still represents one engine serving one GPU. Existing controls remain disabled.

## Prepare private records

Use a new session UUID for each simultaneous experiment. Prepare every participant with the same session ID, complete participant UUID list and enclosure condition. For example, on Ubuntu with the package installed in an isolated environment:

```sh
python -m lab.native_cli --db /private/native.sqlite3 prepare-import \
  --bundle /private/card-a-evidence --engine kmic \
  --model-id approved-model --model-license Apache-2.0 \
  --session-id 33333333-3333-4333-8333-333333333333 \
  --session-gpu GPU-11111111-1111-1111-1111-111111111111 \
  --session-gpu GPU-22222222-2222-2222-2222-222222222222 \
  --enclosure closed --output /private/card-a.json
```

Repeat for card B's evidence and a different output file. Substitute verified local identifiers; these identifiers are examples, not runnable presets. Preparation reads fixed evidence files and never opens paths recorded inside those files, launches inference, or creates the database. `--enclosure` accepts `unknown`, `open` or `closed`. Omitted context remains absent. For a single-card run, `--enclosure closed` records that card as the sole participant without inventing a session ID.

Import and publish through the existing local CLI, after checking each normalized document:

```sh
python -m lab.native_cli --db /private/native.sqlite3 import --file /private/card-a.json
python -m lab.native_cli --db /private/native.sqlite3 import --file /private/card-b.json
python -m lab.native_cli --db /private/native.sqlite3 publish --directory /private/published
```

Keep evidence, UUIDs, normalized JSON and databases private. No hardware IDs, credentials, native paths or real benchmark data are included in this repository. A production image/snapshot change remains a separate reviewed operation.

## Meaning and limits

- Participant lists and enclosure conditions are operator declarations. Telemetry cannot prove case position, PSU identity or that the declared GPUs were the only active devices.
- Each record must include its own measured GPU in a bounded, unique participant list. Multiple participants require a session UUID. Lists are sorted before hashing.
- A session displays a common measured window only when every declared participant appears exactly once in the loaded history, declarations agree, model artifacts/driver/workload hashes match and measured windows overlap. The common window is the intersection of each start time plus its observed duration. It is not the entire host process lifetime.
- Missing, duplicate, non-overlapping or incompatible records receive a warning. The API returns only the newest records; an older participant outside the list window may be missing. This does not establish a GPU failure.
- Failed and thermal-cutoff records retain their per-card outcome. Even with all participants present, any partial or failed measurement prevents the UI saying every card completed its target.
- Per-card speed, cap evidence, window, temperature and board power remain separate. The UI does not sum medians, peaks or average power from different windows, infer wall-socket consumption, certify a PSU, or rank different models as equivalent.
- Existing per-GPU comparison keys remain unchanged. Conditions are displayed in those tables so single-card and simultaneous runs are distinguishable. Session workload keys additionally allow different GPU UUIDs to appear together, without merging their per-card comparison groups.

## Compatibility

The optional `test_context` field is included in the immutable measurement fingerprint when present. Historical v1 documents with absent/null context keep exactly their original fingerprints and remain idempotently importable. SQLite schema and application ID do not change. Existing rows are neither migrated nor relabelled. Adding/changing conditions for an already imported run ID is an idempotency conflict, not an update; supply declarations before first import. Do not assign a new run ID simply to bypass a conflict and duplicate a measurement.

The existing authenticated list/detail/export routes carry the new provenance. No HTTP mutation, engine-control API, host agent, new dependency or privileged mount is added. An older dashboard can ignore the extra provenance; preparing annotated imports requires the new CLI.
