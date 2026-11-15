# Architecture and API

Browser (React/TypeScript) → same-origin FastAPI on Ubuntu → read-only psutil / nvidia-smi, bounded model inventory, SQLite, and fixed native systemd user adapters. Windows reaches Ubuntu through an ordinary SSH port forward. The backend never invokes SSH and never accepts a command, executable, URL, arbitrary unit name or model path from an API caller.

## Native adapters

Kmic, PXA and qr0n use a common NativeAdapter with immutable unit names. systemctl --user is-active provides service state; unavailable means no usable user service manager or unknown unit. This is service status, not process discovery or HTTP readiness. start/stop uses argument arrays with subprocess shell disabled, fixed units, a 20-second timeout and a process-local lock. No engine is installed, compiled or launched on application startup. Exact upstream binary provenance/versions and health protocols remain host configuration work. PXA upstream reference: https://github.com/poisonxa16/pxa (reviewed 2026-11-15; no code bundled).

## API

Every /api endpoint requires Authorization: Bearer TOKEN. No CORS middleware or browser credential cookies. API documentation endpoints are disabled. JSON control and benchmark payloads reject extra fields.

| Method | Path | Purpose |
|---|---|---|
| GET | /healthz | Minimal public liveness |
| GET | /api/system | Backend host CPU, memory, DB filesystem usage and GPUs |
| GET | /api/engines | Fixed three services and control capability |
| GET | /api/models | Configured-root inventory, relative names, bytes, warnings |
| GET | /api/benchmarks?limit=50 | Newest manual results, max 200 |
| POST | /api/benchmarks | Record a manual measurement; does not run inference |
| POST | /api/engines/{kmic,pxa,qr0n}/control | {"action":"start"} or {"action":"stop"}; disabled by default |

Example manual record body:

~~~json
{"engine":"kmic","model":"example.gguf","tokens_per_second":12.5,"notes":"Manual run; 128 output tokens; configuration recorded separately"}
~~~

Use a trusted API client with the bearer token. Keep secrets out of command history and screenshots. Manual results are unverified self-reported measurements; the API records source=manual and a server-side UTC timestamp. Benchmark writes remain enabled with valid authentication even when engine controls are disabled.

## SQLite

benchmarks: id, created_at, engine, model, tokens_per_second, notes, source. audit: id, created_at, engine, action, outcome. Queries use bound parameters and request-scoped connections. A requested audit row is committed before a service operation. Success/failure updates follow it; a crash can leave requested as an intentionally indeterminate outcome. Audit has no token values. One shared token is an administrator capability, so attribution is to that capability rather than individual users. Local host users with DB write access can modify history; this is not a tamper-proof audit store.

## Boundaries

Read operations do not change GPU clocks/power, files, engines or Netdata. SQLite writes are restricted to history/audit. The backend can operate only as its Unix user, but compromised application code would inherit that user's permissions; run a dedicated account for stronger isolation in a future deployment. The model filesystem must be trusted against concurrent malicious changes. No plugin loading, root helper, Docker socket, download endpoint, model switching, restart action or remote shell exists.
