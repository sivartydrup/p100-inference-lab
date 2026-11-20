# Architecture and API

The optional [administrator-started worker](ADMIN_WORKER.md) is a separate root-owned host CLI/runtime and private database, never an API child process or container permission. Fixed system units run only reviewed sealed presets as a bound non-root UID/GID with no capabilities. Effective child checks, non-forgeable approval gates, worker birth identity, sampled thermal/150 W checks, durable intent and interruption recovery apply. It installs no persistent service or privilege grant and requires integrated host acceptance after review.

[Measured GPU sessions](MEASURED_SESSIONS.md) add optional operator-declared session provenance to terminal imports, with no schema migration or new API route. The browser groups only loaded records with matching participant lists, enclosure and workload/driver keys, checks their measured-window intersection, and preserves per-card outcomes and metrics. Legacy document digests are unchanged when context is absent. Session declarations never authorize execution.

Measured resident history is described in [MEASURED_HISTORY.md](MEASURED_HISTORY.md). A local converter crosschecks the fixed archived files, validates a versioned normalized document and recomputes aggregates. Import appends only terminal rows to the separate native database; synthetic import IDs are never executable systemd units. No web/fixture schema migration or HTTP write route is added. A backup/publish command atomically replaces a standalone native snapshot mounted read-only by the optional history overlay. The existing authenticated GET/detail/export API serves imported request, warmup and telemetry evidence. Comparison groups share exact model/workload/prompt hashes and GPU/driver, with cap evidence and partial windows shown explicitly. No host execution privilege is added to the container.

The optional [native runner](NATIVE_RUNNER.md) adds a separate host-only CLI and read-only native history. Base production topology below remains unchanged.

Windows browser → Ubuntu port 3000 → a non-root container containing compiled React/TypeScript assets and FastAPI → host read-only CPU/RAM/NVIDIA telemetry, read-only configured model mounts and a persistent SQLite volume. Docker builds the frontend on Ubuntu; Node is not needed on the production host after image build. Windows has no production role beyond the browser.

## Telemetry and engine adapters

LAB_TELEMETRY is mock (default on Windows) or live (default elsewhere; explicitly set in Compose). Samples identify mode and scope. Mock data is a fixed simulated development fixture, never an inferred P100 result. Live CPU/RAM use /host/proc in Compose; disk is the DB volume filesystem. GPU telemetry uses one fixed nvidia-smi query with a five-second timeout, tolerates absent tools/drivers and unknown metrics, and issues no tuning commands. Netdata is neither reconfigured nor proxied.

NativeAdapter registers only kmic, pxa and qr0n, validates IDs and queries fixed user-service names. It has no control method. Container service state is unavailable because host DBus/systemd are deliberately not mounted. This distinguishes registration from discovery, process health and inference readiness. No engine sources, model weights or engine-specific launch flags are bundled. Exact upstream identities are unverified and must be recorded during host acceptance.

Model roots are an administrator-only JSON array of absolute paths. Scans expose relative names/sizes, skip symlinks and bound matching files/directories. API callers cannot supply roots. Host-local malicious filesystem races are outside this boundary.

## API contract

Every /api route requires `Authorization: Bearer TOKEN`. Unknown/extra payload fields are rejected; floating values must be finite and positive. No CORS or ambient credential cookies. Interactive API docs are disabled.

| Method | Path | Behavior |
|---|---|---|
| GET | /healthz | Minimal public liveness |
| GET | /api/system | Mode/scope, timestamp, CPU, RAM, DB disk, GPUs |
| GET | /api/engines | Three registered adapters, service states, controls_enabled=false |
| GET | /api/models | Relative model filenames/sizes, scan warnings/truncation |
| GET | /api/benchmarks?limit=50 | Newest results, limit 1–200 |
| POST | /api/benchmarks | Store a manual measurement, never run inference |
| POST | /api/engines/{kmic,pxa,qr0n}/control | Reserved strict start/stop schema; always 403 |

Example benchmark payload:

```json
{"engine":"kmic","model":"example.gguf","tokens_per_second":12.5,"notes":"Manual trial, record hardware/preset details here"}
```

Benchmark records are self-reported, not verified measurements. The server adds source=manual and a UTC timestamp. UI shows history; write through an authenticated API client. UI retries five seconds after each completed refresh. A shared 10-second deadline aborts the complete four-endpoint snapshot, including response bodies; failed refreshes cancel outstanding sibling requests. Old readings lose their live label after 15 seconds without a successful snapshot, using browser receipt time rather than the server clock. Returning to a suspended tab immediately rechecks sample age; responses completing after their wall-clock deadline are rejected even if the timeout callback was suspended. Failures/timeouts display a stale-data warning, automatic retries recover the live indicator, and disconnect cancels active requests and clears token/data. In-flight server operations may complete after disconnect.

## Storage and operations

SQLite benchmarks: id, created_at, engine, model, tokens_per_second, notes, source. The inherited empty audit table is reserved for future control design; no host actions are recorded or performed in Phase 1. Connections are request-scoped, SQL values are bound, and Docker stores data in lab_data. Initial tables use CREATE TABLE IF NOT EXISTS; future schema changes require migrations. One Uvicorn worker, bounded container memory/CPU and restart-unless-stopped. Healthcheck validates app liveness, not GPU/engine readiness. DB backup/restore and retention need operational care; no automatic deletion is implemented.

See SECURITY.md for the separate authenticated host-agent proposal. Startup cannot alter native engines, Netdata, SSH, CUDA or drivers.

Deployment references: [Docker Compose GPU reservations](https://docs.docker.com/compose/how-tos/gpu-support/) and [NVIDIA utility driver capability](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html). These are optional host prerequisites, not installed by this project.

## Phase 2 fixture queue

Native history is independent of this fixture queue. Its dedicated SQLite database has a separate application ID/schema; the API optionally opens `LAB_NATIVE_DB` read-only and never creates or migrates it. Authenticated `GET /api/native-benchmarks?limit=50` (1–100), `GET /api/native-benchmarks/{UUID}` and `GET /api/native-benchmarks/{UUID}/export` expose history. Native submission and cancellation POST routes always return 403. The UI polls independently with deadlines, stale indicators and authenticated export. Base Compose has no native database mount, host service bus or native worker.

The administrator CLI validates private presets and artifact hashes, fixes argv/environment, checks resources, persists intent and starts one uniquely named bounded systemd user unit per trial. A private gate checks actual unit policy before native exec. Per-user and per-database locks exclude cooperating workers; unconfirmed cleanup blocks new claims. Recovery stops recorded units and marks interrupted work without replay. No automatic service installation is supplied. The guide documents trusted filesystem/dependency assumptions, GPU reservation requirements and measurement limitations.

See [fixture API, schema and worker](PHASE2.md). API startup adds benchmark_jobs, benchmark_samples and benchmark_job_events atomically and advances SQLite user_version from 0 to 1, preserving Phase 1 benchmarks/audit. A newer schema is refused. Fixture results never populate benchmarks. Base deployment adds read-only job/preset endpoints; writes remain opt-in with a distinct secret. The optional worker shares only the local data volume and runs no native engine commands. Exports read job metadata, samples and events in one SQLite snapshot.
