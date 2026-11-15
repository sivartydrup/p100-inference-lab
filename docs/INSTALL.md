# Ubuntu deployment and verification

Target: trav@192.168.0.188. Deployment is manual in Phase 1. Do not run the backend as root. This guide creates only lab files and optional user services; it does not modify sshd, firewall, GPU drivers, Docker or Netdata.

## 1. Inspect and copy

From Windows, first inspect the destination with an interactive SSH session. Verify the server host key normally; never disable host key checking. Check ~/p100-inference-lab does not contain existing work. If it does, use a different directory and adjust paths below. Transfer the project source without node_modules, .venv, .env or data files using your preferred file transfer tool. Do not overwrite existing inference directories. Build frontend assets either locally or on Ubuntu, then copy frontend/dist too.

~~~bash
ssh trav@192.168.0.188
ls -ld ~/p100-inference-lab ~/inference 2>/dev/null
~~~

Check python3 --version, node --version, nvidia-smi, and existing engine paths manually. Python 3.11+ is needed. Engine commands differ between forks: verify --help and existing launch commands rather than assuming P100 flags.

## 2. Install into an isolated environment

On Ubuntu, from ~/p100-inference-lab:

~~~bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python -m pytest -q
cd ../frontend
npm ci
npm run build
~~~

If you built the dashboard on Windows and copied dist, Node is not required on Ubuntu. Install Python venv support through your normal host maintenance process if absent.

## 3. Configure a private environment file

Create backend/.env with a text editor, permissions 600, and a random token from python3 -c 'import secrets; print(secrets.token_urlsafe(48))'. Do not commit or share it. Example (replace the token):

~~~text
LAB_API_TOKEN=REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS
LAB_ENABLE_CONTROLS=false
LAB_MODEL_ROOTS='["/home/trav/inference/models"]'
LAB_DB=/home/trav/p100-inference-lab/backend/data/lab.sqlite3
LAB_STATIC_DIR=/home/trav/p100-inference-lab/frontend/dist
~~~

The Python app itself does not load .env. For a foreground run, use a trusted shell environment file:

~~~bash
cd ~/p100-inference-lab/backend
chmod 600 .env
set -a
. ./.env
set +a
.venv/bin/uvicorn lab.main:app --host 127.0.0.1 --port 8000 --workers 1
~~~

Use one worker: the operation lock is process-local. Do not add workers. Keep model roots narrow; scanning is capped at 1,000 matching files and 5,000 directories. For large libraries, refresh every five seconds may still be expensive; choose a small curated root.

## 4. Access from this Windows PC

~~~powershell
ssh -N -L 8000:127.0.0.1:8000 trav@192.168.0.188
~~~

Open http://127.0.0.1:8000 in your browser. Enter the token. It stays in React memory only and is cleared on disconnect or reload. The dashboard and API share an origin. All /api routes require the token, including read endpoints; /healthz reports only status. Do not bind to 0.0.0.0 or open a LAN control port. For any future LAN deployment, add a properly configured HTTPS authentication gateway and review the threat model first. Netdata remains at its existing address and is not proxied by this app.

## 5. Optional backend user service

Review deploy/p100-lab-api.service.example. Ensure its paths match your checkout and environment file. Copy it to ~/.config/systemd/user/p100-lab-api.service, then run systemctl --user daemon-reload and systemctl --user start p100-lab-api. No enable or lingering is required for a foreground experiment. If you later need service persistence after logout, decide that separately with the host administrator. Check journalctl --user -u p100-lab-api for diagnostics.

## 6. Optional engine controls — explicit opt-in

Leave controls disabled until existing engine processes and GPU ownership have been inspected. The adapters operate only these exact user units:

- p100-lab-kmic.service
- p100-lab-pxa.service
- p100-lab-qr0n.service

Use deploy/p100-lab-engine.service.example as a template, one copy per engine. The template intentionally has no usable executable path. Replace its ExecStart with the **verified absolute binary and fixed arguments** for that engine. Bind inference listeners to loopback, choose distinct ports, and confirm model paths manually. No shell wrappers, sudo rules or privileged system services are needed. Review upstream licenses and compatibility separately. This project does not assume Kmic or qr0n upstream identities from their informal labels.

If an engine already runs under another name, leave it alone; the dashboard will not discover it. Do not create a second competing process. Only after preparing and testing dedicated lab units, set LAB_ENABLE_CONTROLS=true and restart the API. Start/stop can interrupt inference and multiple active engines can exhaust GPU memory. Phase 1 does not coordinate shared GPUs or prevent overlap. A timeout can leave a systemd job in progress: inspect state before retrying.

## Host acceptance checklist

- Existing SSH session and Netdata dashboard still work.
- Backend is listening only on 127.0.0.1:8000; tunnel works.
- /healthz succeeds; /api/system without a token returns 401.
- CPU/RAM match host tools; GPU absence is graceful; when installed, GPU readings match nvidia-smi.
- Only configured model files appear; no host absolute paths are exposed in inventory.
- Controls return 403 by default. With opt-in, test start/stop on a dedicated harmless lab unit before a real workload; inspect SQLite audit rows.
- Record a manual benchmark through the API; restart the backend and confirm it persists.

## Rollback and backup

Stop only p100-lab-api and lab engine units you deliberately installed. Remove those user unit files and run systemctl --user daemon-reload. Keep engine binaries and existing inference/Netdata installations intact. Stop the API before copying backend/data/lab.sqlite3 for a simple consistent backup. The token and model root configuration are separate private backups. No automatic database migration beyond initial table creation is implemented yet.
