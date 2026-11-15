# Architecture and API

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

Benchmark records are self-reported, not verified measurements. The server adds source=manual and a UTC timestamp. UI shows history; write through an authenticated API client. UI retries five seconds after each completed refresh. A shared 10-second deadline aborts the complete four-endpoint snapshot, including response bodies; failed refreshes cancel outstanding sibling requests. Old readings lose their live label after 15 seconds without a successful snapshot, using browser receipt time rather than the server clock. Returning to a suspended tab immediately rechecks sample age. Failures/timeouts display a stale-data warning, automatic retries recover the live indicator, and disconnect cancels active requests and clears token/data. In-flight server operations may complete after disconnect.

## Storage and operations

SQLite benchmarks: id, created_at, engine, model, tokens_per_second, notes, source. The inherited empty audit table is reserved for future control design; no host actions are recorded or performed in Phase 1. Connections are request-scoped, SQL values are bound, and Docker stores data in lab_data. Initial tables use CREATE TABLE IF NOT EXISTS; future schema changes require migrations. One Uvicorn worker, bounded container memory/CPU and restart-unless-stopped. Healthcheck validates app liveness, not GPU/engine readiness. DB backup/restore and retention need operational care; no automatic deletion is implemented.

See SECURITY.md for the separate authenticated host-agent proposal. Startup cannot alter native engines, Netdata, SSH, CUDA or drivers.

Deployment references: [Docker Compose GPU reservations](https://docs.docker.com/compose/how-tos/gpu-support/) and [NVIDIA utility driver capability](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html). These are optional host prerequisites, not installed by this project.
