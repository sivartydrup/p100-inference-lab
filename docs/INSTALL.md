# Manual Ubuntu 24.04 deployment

Target: 192.168.0.188. Everything operational runs on Ubuntu; no Windows service, shared drive or SSH connection is required for normal LAN operation. This guide is for a reviewed release, not an instruction to deploy this PR automatically.

## Preflight

1. Keep a working SSH session. Confirm existing Netdata, engine processes/listeners and `nvidia-smi` behavior. Record them for comparison after deployment.
2. Check Docker Engine, Compose v2, disk capacity and that 192.168.0.188 belongs to this host. Check port 3000 is unused (`ss -ltn`). Do not stop an existing listener to make room.
3. Use a new checkout directory; preserve any existing work and model/engine directories. Review the Compose files. They create only project containers, network and a named SQLite volume.
4. GPU telemetry additionally requires an already configured NVIDIA Container Toolkit. If absent, deploy the base stack without GPU support and schedule toolkit installation separately. Do not upgrade drivers or CUDA for this application.

## Configure and start after approval

From the checkout, create `secrets/api_token.txt` with a cryptographically random token of at least 32 characters (README command). The containing directory is mode 700; the file is readable by container UID 10001 via the Docker secret bind mount. Compose local secrets do not encrypt the source file. Keep it out of Git, backups without encryption, logs and screenshots. Do not use a frontend build variable for the token.

```bash
# Without GPU telemetry:
docker compose up -d --build
# OR with existing NVIDIA toolkit, choose this consistently for subsequent commands:
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

Open **http://192.168.0.188:3000** on Windows and enter the token. Container port 8000 maps only to that specific host IP on port 3000. There is no separately published API port. Restart policy resumes the app after Docker/host restart when the Docker service is enabled through the host's normal administration process.

HTTP exposes bearer tokens to network observers. Use this URL only on a trusted isolated LAN. Do not publish it on the internet or add router forwarding. For untrusted networks, set `LAB_BIND_IP=127.0.0.1` in the Compose shell environment before starting, then use `ssh -N -L 3000:127.0.0.1:3000 USER@192.168.0.188` and open http://127.0.0.1:3000, or configure an independently reviewed HTTPS gateway. No SSH/firewall configuration changes are needed by the app. Docker port publication may bypass host firewall rules; binding an IP is not a client allowlist.

## Monitoring and inventory

The API mounts `/proc` read-only at `/host/proc` and uses psutil's PROCFS_PATH to read host CPU/RAM rather than silently reporting container RAM limits. Disk metrics describe the filesystem holding `/data`, not every host drive. It needs no privileged mode, host PID namespace, host root filesystem or Docker socket. `/proc` still contains host metadata: treat the container as trusted software. Base Compose reports NVIDIA unavailable; the optional overlay requests GPUs and only the NVIDIA `utility` driver capability for nvidia-smi, with no clock/power write commands. This is an application-level read-only policy, not a kernel-level guarantee that all GPU ioctls are read-only.

Kmic, PXA and qr0n are registered by stable IDs. A native Python run can query fixed `p100-lab-{id}.service` user units read-only. **The container intentionally has no host systemd/DBus access**, so it reports service state unavailable; this never means an existing engine is absent or stopped. Do not create duplicate services. Exact installed paths, upstream revisions, current service names, ports and readiness protocols must be inspected before implementing live engine integration. No invented upstream URLs or launch arguments are assumed.

Model inventory defaults to empty. To enable it, create a private Compose override with a narrow read-only mount and the corresponding container path:

```yaml
services:
  lab:
    environment:
      LAB_MODEL_ROOTS: '["/models"]'
    volumes:
      - type: bind
        source: /verified/existing/model-directory
        target: /models
        read_only: true
        bind:
          create_host_path: false
```

Keep that file private; pass it with `-f compose.yaml -f YOUR_OVERRIDE.yaml` (and the GPU overlay if used). No model content is read, downloaded, loaded or modified: the inventory returns relative filename and size only. Scans stop at 1000 matching files or 5000 directories, skip symlinks and refresh with the UI every five seconds. Use a curated directory; limits do not bound sorting/stat time in a huge single directory. Trusted local filesystem required.

## Acceptance

- `docker compose ps` reports healthy; `/healthz` returns only status.
- Dashboard loads at the target URL; all four `/api/*` reads return 401 without a token.
- Authenticated live CPU/RAM agree with host tools; optional NVIDIA samples agree with `nvidia-smi` (including GTX 950 or no P100 yet). Unknown power values display unavailable.
- Service state is explicitly unavailable in containers. Controls remain disabled, including with `LAB_ENABLE_CONTROLS=true` accidentally set.
- A manual result recorded through POST `/api/benchmarks` persists after container recreation. Mock samples are not benchmark history.
- Existing SSH, Netdata and native engines still operate as before; no duplicate GPU workloads started.
- Restart the host only during an approved maintenance window to validate boot persistence.

## Backup and rollback

Stop only this project's container before backing up its named volume with a normal Docker volume backup procedure; this keeps SQLite consistent. Back up the private token/config separately with encryption. Never use `docker compose down -v` on production history. `docker compose down` removes only project containers/network and preserves history; it does not touch Netdata, drivers, models or native engines. Use a tested previous image/commit for rollback. Initial schema uses CREATE TABLE IF NOT EXISTS; schema migrations will be added before evolving stored data.

The files in deploy/ are retained historical native-service examples, not part of Compose and not required or recommended for Phase 1. Do not install the engine example or enable controls from old instructions.

## NVIDIA runtime activation verified on this host

The Ubuntu GTX 950 host was validated with NVIDIA Container Toolkit 1.20.1-1 and the existing driver 580.178.04. Only the four container-toolkit packages were added; the driver and CUDA were not upgraded. The reviewed Docker configuration change registered only runtimes.nvidia and was activated with `systemctl reload docker`, leaving the default runtime and existing containers unchanged.

The GPU overlay uses `runtime: nvidia`, `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=utility`. Select the runtime explicitly: on this already-running Docker daemon, registration reloaded successfully but `--gpus all` still failed to select a GPU device driver after toolkit installation. Direct runtime selection passed without a Docker restart. Do not restart Docker or alter native inference software merely to make the alternate GPU selector work. Other hosts still require their own compatibility checks.

This validation queried GPU health only; it did not run CUDA inference or establish P100 compatibility/performance. Keep the installed native engine/CUDA environment independent.
