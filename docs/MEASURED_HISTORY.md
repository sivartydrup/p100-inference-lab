# Import measured resident history (read-only dashboard)

Version 0.4 adds a local operator workflow for archived resident-model measurements. It does not launch inference, enable a worker, add an import API, or modify a driver, power policy, dashboard boot guard or existing production deployment. Manual records, simulated fixture jobs, managed native CLI trials and imported resident measurements remain distinguishable.

## Trust and measurement meaning

Imports are **operator assertions backed by identified evidence**, not signed hardware attestations. SHA-256 identifies the supplied evidence; it does not prove its origin or correctness. Strict validation rejects ambiguous JSON/CSV, non-finite numbers, unsupported protocols, inconsistent workload/timing counts, out-of-order samples, cap-evidence mismatches and misleading completion claims. The converter crosschecks raw server timing, per-request CSV and the recorded summary; stored aggregates are recomputed from validated samples. It never executes a log command, opens a model/binary path mentioned in metadata, imports arbitrary modules, or reads files named by log content.

The reviewed source is the fixed resident acceptance harness: one model load, one excluded warmup, sequential /completion SSE requests, token-ID prompt, greedy sampling, ignored EOS, no prompt cache and one slot. The converter supports the existing completed_600_seconds and temperature_cutoff outcomes. First-text latency measures request start to first nonempty streamed content over a private Unix socket, **not exact first-token TTFT**, chat-template cost or Windows-browser latency. Decode throughput uses the server's convention: generated tokens minus the first token divided by decode duration. No answer quality or wall-socket energy is inferred. Temperatures, board power and VRAM are samples that may miss transients.

A cutoff remains a failed/partial terminal record with its measured samples and partial statistics; it is never relabelled a completed ten-minute test. A power cap is marked logged only if requested and enforced values match it in every reading. Otherwise the cap is explicitly operator-declared or unknown. A configured cap is separate from observed board draw and does not establish an absolute instantaneous ceiling.

Comparison groups require identical model hashes, prompt token-ID hash/settings, GPU UUID and driver. Engine and power cap may differ; the table exposes those differences, outcomes and durations. Single runs, different initial temperatures, different upstream defaults and uncontrolled ambient conditions limit conclusions. Separate model sizes/protocols are not grouped together.

## Local operator workflow

Use a dedicated native database, never the dashboard/manual/fixture database. No schema migration is needed: imported runs append immutable terminal rows to the native store's existing schema/application identity. They never enter its queue, create a systemd unit, or become cancellable execution jobs. UUID idempotency keys make identical reimports harmless; changed content with the same ID is refused. Managed native execution remains independently gated and disabled by default. Import/publish work on Windows for development as well as Ubuntu; only Ubuntu is production.

With the backend installed in a private virtual environment and a reviewed acceptance directory:

```bash
umask 077
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 init
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 prepare-import \
  --bundle /PRIVATE/acceptance/results --engine kmic \
  --model-id qwen25-7b-instruct-q4-k-m --model-license Apache-2.0 \
  --output /PRIVATE/reviewed-run.json
# Inspect the normalized file before importing. Do not publish private machine data in Git.
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 import --file /PRIVATE/reviewed-run.json
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 publish --directory /PRIVATE/published-history
```

`prepare-import` retains the CLI's --db syntax but never opens that database. It reads only metadata.json, summary.json, measurement-start.json, request.json, warmup.json, requests.csv, telemetry.csv and timings.jsonl. Each file is bounded at 8 MiB, symlink files are refused, requests are limited to 1000 and telemetry to 3600 readings. Existing output files are not overwritten. No ZIP extraction or remote download is supported. The initial process-per-trial llama-bench acceptance logs use a different protocol and are not converted by this resident adapter.

For older logs without cap columns, optionally supply `--declared-power-limit 250`. This remains labelled **declared**, not continuously verified. Without that flag, missing cap evidence remains unknown. Use `--engine qr0n` for that installed engine; its recorded engine identity, when present, must agree. Model license is an explicit operator-reviewed input; no license is inferred from a filename.

Normalized JSON uses `p100-resident-measurement-v1`, strict extra-field rejection and offset-aware timestamps. It contains sanitized identifiers/hashes, workload, measured window/outcome, excluded warmup, per-request timing and telemetry. Native paths, original argv, generated content, credentials and arbitrary generation settings are excluded. Imports are bounded at 8 MiB. GPU UUID/build metadata can still be sensitive: protect the files and authenticated exports. The directory and worker/Python installation remain trusted local operator inputs; this is not a hostile-filesystem sandbox.

## Publish a snapshot, then opt in to the mount

`publish` uses SQLite's backup API with a 30-second progress deadline to create a consistent standalone snapshot at `DIRECTORY/native.sqlite3`, closes it, fsyncs it and atomically replaces the previous snapshot. It refuses a source/destination collision, destination symlinks and overwriting an unrelated database. The directory is created privately (0700); published files use 0644 so replacement preserves read access granted through the directory. No journal/source database or credentials need to be mounted into the web container. Existing request readers can finish on the old inode on Ubuntu; new requests see the replacement. Publish only on a local filesystem. Network filesystems and hostile concurrent path replacement are unsupported.

After review and deployment approval, arrange read/traverse access for container UID 10001 to **only the published directory**, for example on Ubuntu with ACL tools already installed:

```bash
sudo setfacl -m u:10001:rx /PRIVATE/published-history
```

Do not loosen permissions on native engines, models, the private source database, secrets or whole home directories. Directory ACL/traversal must be checked on the actual Docker host. The app opens the snapshot read-only and requires the existing API bearer token for every history/detail/export request. There is no unauthenticated LAN management capability.

The optional `compose.history.yaml` overlay requires an existing published directory and binds it read-only; it refuses silently creating a missing host path. Add it to the **existing reviewed deployment's complete Compose file list**, preserving the production image, secrets, GPU overlay and project name. For a source checkout example only:

```bash
export LAB_NATIVE_HISTORY_DIR=/PRIVATE/published-history
docker compose -f compose.yaml -f compose.gpu.yaml -f compose.history.yaml config --quiet
# Deployment/recreation is a separate approved step; this guide does not execute it.
```

The overlay sets only LAB_NATIVE_DB and the read-only snapshot mount on lab. It starts no worker and adds no host bus, Docker socket, model mount or port. The validated production host's early 150 W units and dashboard network-recovery unit must be preserved during any later image update. Base Compose remains disconnected from native history. Publishing a snapshot alone does not deploy the code or change production configuration.

## Validation and current limitations

Tests cover raw evidence crosschecks, duplicate/non-finite/oversized inputs, terminal-only import, idempotency/conflicts, cutoff/partial handling, warmup exclusion, comparison isolation, read-only API/export, consistent snapshots, protection of unrelated data, and real container mount write rejection. Browser tests check imported labels, declared/logged caps, partial windows, first-text semantics, export authentication, absent management controls and mobile layout. Linux native subprocess tests remain in the full suite; no real engine is launched by these history tests.

The raw frontend audit reports the existing Vite -> tinyglobby/fdir -> picomatch build dependency under [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), with no patched release available as of 18 November 2026. PR #4 proposes an explicit temporary risk acceptance in frontend/security-exception.json, expiring **2026-11-25T00:00:00Z**. It permits only picomatch 4.0.7, fdir 6.5.0, tinyglobby 0.2.17 and Vite 7.3.8 at their exact existing development-only lockfile paths, with all high findings tracing solely to that advisory.

`npm run audit:security` runs eight policy tests, preserves the complete unmodified npm JSON report, validates the exception and separately runs `npm audit --omit=dev --audit-level=high`. New high/critical advisories, critical escalation, changed versions/paths, production exposure, malformed reports or expiry fail. CI retains the raw JSON as an artifact and prints the accepted risk and expiry. This is risk acceptance, **not a vulnerability fix or a clean full audit**; it remains a proposed policy change for owner review while the PR is a draft. Production contains static assets and Python without the Node build toolchain; development/build inputs must remain trusted. Remove this exception when upstream remediation is available. There is no automatic renewal, forced downgrade, server change or deployment.

Actual Ubuntu deployment/ACL acceptance and UI integration with the production snapshot await approval. Importing external resident results does **not** validate the separate native runner's systemd execution gate, unit cleanup or real-engine parser under systemd. Native engine/model management remains disabled.
