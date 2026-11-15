# P100 Inference Lab

A small, native Ubuntu inference management lab with a React/TypeScript dashboard, FastAPI backend and SQLite history. Designed for the P100 host at 192.168.0.188; not tied to a particular GPU count.

**Phase 1:** live CPU, RAM, lab-disk and NVIDIA GPU monitoring; Kmic/PXA/qr0n user-service status; bounded read-only GGUF and safetensors inventory; manual benchmark history; authenticated, opt-in start/stop controls. No model loader, downloads, inference runner, tuning or arbitrary command endpoint.

The backend measures **the machine where it runs**. Run it on Ubuntu for server telemetry. Windows development metrics are not remote server metrics. Service activity is not an engine readiness check. Existing installations are not auto-discovered or changed. No SSH or Netdata configuration is changed by this project.

## Start here

See [installation](docs/INSTALL.md), [architecture and API](docs/ARCHITECTURE.md), [security](SECURITY.md), and [benchmark/tuning roadmap](docs/ROADMAP.md). Engine service examples are in deploy/ and must be adapted to verified existing binaries before use.

## Development

Python 3.11+ and Node 22.12+ (or supported newer Node) are required.

~~~bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
export LAB_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
# Keep this token private; use the same value in the dashboard.
uvicorn lab.main:app --host 127.0.0.1 --port 8000
~~~

In another terminal:

~~~bash
cd frontend
npm ci
npm run dev
~~~

Open http://127.0.0.1:5173. Vite proxies /api to the local backend; do not expose the development server on the LAN. Windows: activate .venv/Scripts/Activate.ps1 and set environment variables with PowerShell. Backend imports do not run commands or create a database; startup requires a token of at least 32 characters.

## Validation

~~~bash
cd backend
python -m pytest -q
cd ../frontend
npm ci
npm run build
npm audit --audit-level=high
npx playwright install chromium
npm test
~~~

Tests cover authentication, disabled controls, strict inputs, fixed command arguments, audit outcomes, missing GPU/systemd handling, inventory bounds/symlinks, and benchmark persistence. CI runs backend tests on Ubuntu and builds the dashboard, then runs browser checks with fixture data. Browser checks verify authentication errors, disabled controls, token clearing and mobile layout. Real P100 telemetry and user services require host acceptance checks in the installation guide.

## License decision pending

**Proposed: MIT.** The complete candidate text is in [LICENSE.proposed](LICENSE.proposed). It is not an approved license grant yet. Confirm MIT (or choose Apache-2.0) before renaming it to LICENSE and publishing. Third-party engines, dependencies and model weights retain their own licenses. No engine sources or weights are bundled.

No GitHub repository has been created. Before publishing, confirm repository name, visibility and license.
