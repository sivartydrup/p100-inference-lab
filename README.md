# P100 Inference Lab

An Ubuntu-first, open-source observation and benchmarking foundation for native NVIDIA inference engines. React/TypeScript UI, Python FastAPI API and persistent SQLite history, packaged together in Docker Compose. Production runs wholly on the Ubuntu 24.04 server; Windows is development and browser access only.

## Delivered foundation

- Authenticated CPU/RAM, database-disk and NVIDIA GPU telemetry; graceful operation without GPUs.
- Shared, approximately once-per-second authenticated telemetry stream with polling fallback; see [live telemetry](docs/LIVE_TELEMETRY.md).
- Clearly labelled simulated telemetry for Windows development (`LAB_TELEMETRY=mock`). Mock values are never benchmark results.
- Read-only adapter inventory for Kmic, PXA and qr0n; bounded GGUF/safetensors file inventory.
- Manual benchmark recording through the authenticated API and history in the dashboard.
- Non-root container, persistent database volume, optional NVIDIA utility integration, tests and CI.

Engine/model controls are **not implemented**. The reserved control endpoint always returns 403 after authentication. There is no arbitrary command API, shell interpreter, SSH automation, model downloader, driver installer or Docker socket mount. Existing Netdata and native engines remain independent.

The optional [administrator-started worker](docs/ADMIN_WORKER.md) adds a disabled, host-only system-unit transport with non-root engine payloads, sealed presets and a private durable queue. It installs no service or privilege grant and is not invoked by the dashboard. Review and actual host acceptance are required before enabling it.

## Phase 2 benchmark foundation (fixture-only)

The optional [fixture benchmark lab](docs/PHASE2.md) adds a durable job queue, separate non-root single-job worker, strict presets, cancellation/deadlines, raw warmup/trial samples, summary statistics, provenance and JSON export. It uses deterministic simulated metrics, never launches a native engine, and never adds synthetic results to manual benchmark history.

Job submission/cancellation are disabled by default. Enabling fixtures requires a second independent secret; the normal dashboard token alone cannot queue jobs. The worker has no network, GPU devices, host proc mount, Docker socket, model files or authentication secrets in the provided Compose setup. Read the opt-in guide before running it. No Phase 2 production deployment is automatic.

## Ubuntu deployment (manual, after review)

[Measured history](docs/MEASURED_HISTORY.md) adds local-only import of archived resident tests, partial/cutoff records, first-streamed-text and temperature/power summaries, grouped comparisons and JSON export. The dashboard reads an explicitly published SQLite snapshot through the opt-in `compose.history.yaml` overlay. Import never queues inference; there is no browser import/execute/cancel capability. Base deployment stays unchanged. The upstream picomatch build-tool advisory and proposed expiring, version-pinned development-only exception are documented in that guide; raw findings remain visible and production auditing stays strict.

[Multi-GPU measured sessions](docs/MEASURED_SESSIONS.md) link per-card records from simultaneous experiments and display operator-declared case conditions. Missing participants, incompatible workloads and non-overlapping windows are flagged. Per-card results remain separate; this adds no scheduling or engine controls.

The next Phase 2 increment adds an optional [host-only native benchmark runner](docs/NATIVE_RUNNER.md) and read-only native history page. Administrator-reviewed Kmic/qr0n presets have artifact hashes, resource checks, bounded systemd user units, cancellation/recovery and a separate database. It ships disabled, exposes no HTTP execution capability, and is not enabled by Compose. Ubuntu/systemd, compatible model and P100 acceptance remain required; PXA remains inventory-only. See [verification](docs/NATIVE_VERIFICATION.md).

See [installation and acceptance checks](docs/INSTALL.md). The intended browser URL is **http://192.168.0.188:3000**. The static UI is public; every `/api/*` operation requires a random bearer token. Plain HTTP does not encrypt that token; use only a trusted isolated LAN, or choose the documented SSH tunnel/HTTPS alternative. Management remains disabled in every topology.

```bash
# On Ubuntu, after checking prerequisites and reviewing the installation guide:
mkdir -p secrets
python3 -c 'import secrets,pathlib; pathlib.Path("secrets/api_token.txt").write_text(secrets.token_urlsafe(48))'
chmod 700 secrets
chmod 644 secrets/api_token.txt # non-root container must read the bind-mounted secret
# Host directory stays private; do not commit this file.
docker compose up -d --build
```

No automatic deployment is configured. The optional fixture worker is not enabled by base Compose. Docker Engine/Compose and NVIDIA Container Toolkit, if GPU monitoring is wanted, must already be installed and approved. The app never changes drivers, CUDA, SSH, Netdata or engine services.

## Windows development

Python 3.11+ and Node 22.12+ required. In PowerShell:

```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[test]'
$env:LAB_API_TOKEN = .venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(48))'
$env:LAB_TELEMETRY = 'mock'
.venv\Scripts\python.exe -m uvicorn lab.main:app --host 127.0.0.1 --port 8000
```

Keep the generated token private; enter it in the dashboard. In another terminal, `cd frontend`, `npm ci`, `npm run dev`. Open http://127.0.0.1:5173. Vite proxies the API locally. The token stays in tab memory only.

## Checks

```powershell
backend\.venv\Scripts\python.exe -m pytest -q backend/tests
cd frontend
npm ci
npm run build
npx playwright install chromium
npm test
npm audit --audit-level=high
cd ..
docker compose config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
docker build -t p100-lab:phase1 .
backend\.venv\Scripts\python.exe tests/container_smoke.py
```

CI runs backend tests, browser tests, frontend build/audit and the Linux container smoke test. Real server/GPU acceptance is manual and separate from mock tests.

Read [architecture](docs/ARCHITECTURE.md), [security/control design](SECURITY.md), [roadmap](docs/ROADMAP.md), [dependency licenses](docs/LICENSES.md) and [contributing](CONTRIBUTING.md).

## License

Apache-2.0 for this project. Dependencies, container OS packages, external engines and model weights retain their own licenses. No engines, NVIDIA libraries or models are bundled in the source. Exact engine upstreams and licenses require host verification before integration or redistribution.
