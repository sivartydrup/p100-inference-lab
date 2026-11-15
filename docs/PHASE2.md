# Phase 2 foundation: deterministic fixture jobs

This release tests the benchmark workflow, not hardware performance. It never starts Kmic/PXA/qr0n, reads weights, runs inference or downloads a model. Synthetic results always carry source=fixture and remain separate from Phase 1 manual history. A fixture result is never evidence of correctness, throughput, temperature or energy usage on any GPU.

## Development opt-in

For native development, use an isolated development SQLite path. In the API terminal, set LAB_ENABLE_FIXTURE_JOBS=true and generate a new independent LAB_JOB_TOKEN with Python secrets.token_urlsafe(48). Keep LAB_API_TOKEN from the normal setup, with both secrets at least 32 characters and unequal. The optional file forms are LAB_API_TOKEN_FILE and LAB_JOB_TOKEN_FILE. No credentials belong in the frontend build.

```powershell
# In a development API terminal, from backend/:
$env:LAB_ENABLE_FIXTURE_JOBS = 'true'
$env:LAB_JOB_TOKEN = .venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))'
$env:LAB_DB = 'data/fixture-development.sqlite3'
$env:LAB_TELEMETRY = 'mock'
# LAB_API_TOKEN must already hold its separate random credential.
.venv\Scripts\python.exe -m uvicorn lab.main:app --host 127.0.0.1 --port 8000
```

In a second backend terminal, set LAB_DB to the **same absolute database path** and LAB_ENABLE_FIXTURE_JOBS=true, then run `.venv\Scripts\python.exe -m lab.worker`. The worker needs no API/job secret. Start the React development UI normally. Enter the viewer token to read history, then the separate job token in the fixture section to submit/cancel. Read-only access alone cannot submit. The successful and failure presets exercise success, failure and recovery; a short deadline can exercise timeout. A queued job waits while the worker is offline.

For an isolated Ubuntu development checkout with Docker, create secrets/job_token.txt independently of secrets/api_token.txt; keep the secrets directory mode 700 and files readable by container UID 10001. Review the configuration, then explicitly opt in:

```bash
# Defaults to loopback here to keep development private.
LAB_BIND_IP=127.0.0.1 docker compose -p p100-fixture-dev \
  -f compose.yaml -f compose.fixtures.yaml --profile fixtures up -d --build
```

The separate project name gives this development stack its own database volume. The fixture worker has network_mode=none and no GPU/proc/model mounts or secrets; it only shares that stack's local data volume. Its healthcheck is disabled because it has no HTTP service; job state indicates completion, not a worker-health promise. Do not add the GPU overlay for fixture processing. Base Compose does not opt in or start this worker. Do not target the deployed production checkout/volume without a separate reviewed rollout. No deployment is performed by CI.

## API

All endpoints require `Authorization: Bearer <viewer token>`. Mutations additionally require `X-Lab-Job-Token: <job token>` and an explicitly enabled server.

| Method | Path | Behavior |
|---|---|---|
| GET | /api/benchmark-presets | Two fixed synthetic presets, opt-in flag and queue bound |
| GET | /api/benchmark-jobs?limit=50 | Newest metadata/results; limit 1-100 |
| POST | /api/benchmark-jobs | Submit with UUID Idempotency-Key; 201 new / 200 replay |
| GET | /api/benchmark-jobs/{UUID} | Metadata, raw samples and event history |
| GET | /api/benchmark-jobs/{UUID}/export | Authenticated JSON attachment, including synthetic provenance |
| POST | /api/benchmark-jobs/{UUID}/cancel | Cancel queued work immediately, or request cooperative cancellation |

Submission payload (no additional fields accepted):

```json
{"preset":"fixture-v1","trials":5,"warmup":1,"timeout_seconds":30}
```

Presets are fixture-v1 and fixture-failure-v1. Trials: integer 1-20; warmup: integer 0-5; deadline: finite 0.1-60 seconds. Engine/model paths, shell strings, flags, arbitrary URLs and custom prompts are forbidden. Reusing an idempotency key with different settings returns 409. At most 20 queued/running jobs fit in the queue; excess submissions return 429 and Retry-After: 5. UUIDs, numeric bounds and extra fields are validated before insertion. Missing jobs return 404. Disabled or insufficiently authorized writes return 403.

The UI preserves the request ID and settings after a failed/uncertain submission so retry cannot create duplicates. Disconnect/reload clears credentials and retry state; it cannot undo an accepted server request. Check job history before submitting again after a reload. Job tokens are never persisted or exported. JSON downloads use authenticated fetch, not credential-bearing URLs.

## Worker and lifecycle

```text
queued -> running -> succeeded | failed | timed_out | cancelled | interrupted
queued -> cancelled
```

One worker holds a nonblocking OS lock next to the canonical database path. The kernel releases it on exit/crash. Only after acquiring it does startup recover orphaned running jobs as interrupted, preserve partial raw samples and continue with queued work. An interrupted job is not silently rerun. A second worker refuses to run; SQLite transactions also enforce one running job when claiming work. No distributed/network-filesystem worker support is implied.

The fixture worker steps at short fixed intervals, checking cancellation, shutdown and a monotonic deadline before each sample and before success. SIGTERM/SIGINT trigger interruption, and hard kills are recovered at next start. Cancellation and completion serialize in the database; a cancellation committed before final success prevents a successful summary. Partial samples/events remain available for failed/cancelled/timed-out/interrupted jobs, with no successful summary. All terminal states allow subsequent queued work to proceed.

## Data and reproducibility

SQLite user_version 0 -> 1 is an additive transaction creating benchmark_jobs, benchmark_samples and benchmark_job_events plus indexes. Existing benchmarks/audit are not rewritten. The migration is idempotent and refuses newer schema versions. Back up the stopped API **and worker's** shared volume before upgrades. Stop the optional worker too when rolling back. Existing Phase 1 rows remain readable, but an older frontend has no fixture UI. Retention, deletion and multiuser attribution are not implemented; export and back up results and monitor database growth.

Jobs store UTC timestamps, immutable normalized settings, definition/preset hashes, fixture engine/model IDs, fixed seed and prompt hash, explicit synthetic model/license identifiers and nullable hardware/CUDA/driver fields. SHA-256 for the fixture model describes a named no-weights marker, not an actual model file. No hardware or CUDA identity is invented. At each warmup/measured step the worker stores synthetic prompt/output token counts, TTFT, prefill/decode rates and wall duration. Successful summaries exclude warmup and report measured sample count plus median/min/max/population standard deviation. The simulated durations are fixed formula values, not wall-clock execution speed; the job deadline still bounds real worker time.

Exports read metadata, samples and events under one SQLite read transaction. All credentials remain outside the database. The shared job capability is not a per-user identity and the audit is not tamper-proof against local administrators.

## Validation and remaining work

Backend tests cover permissions/opt-in, strict inputs, duplicate/concurrent requests, queue bounds, one active job, deterministic samples/statistics, cancellation/completion races, failure/deadline/shutdown, kernel lock exclusion/release after killed processes, crash recovery, migration preservation and export separation. Browser tests cover read-only history, credential clearing, retry IDs, cancel/export, fixture labels and existing telemetry staleness behavior. Container smoke runs an actual separate non-root offline worker against the API/shared volume, exercises errors/timeouts/cancellation and rejects a second worker.

Real Phase 2 workloads need a usable model with verified provenance/license, compatible GPU hardware, pinned native artifact identities and release-specific readiness/inference protocols. Host inventory found P100-targeted builds and no full model in inspected locations; the currently installed GTX 950 is not the P100 baseline. PXA's prebuilt release differs from its source checkout and includes separately restricted PXQN code. Do not assume all engine forks share flags or tuning defaults. A separately reviewed native worker boundary, GPU ownership, resource preflight, fixed launch presets and authenticated permission separation must precede real engine execution.
