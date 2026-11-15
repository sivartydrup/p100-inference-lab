# Phase 1 verification — 15 November 2026

Existing local source was preserved in the feature branch's first commit before adaptation. The initially empty public repository received only an empty baseline commit on main to permit a PR; application code remains on feat/phase-1-ubuntu-scaffold. Historical pre-implementation notes are preserved separately in LEGACY_HANDOFF.md and do not define current behavior.

## Checks performed locally

- Windows backend: 26 passed, 1 skipped (symlink creation permission unavailable).
- Linux container backend: 27 passed, including symlink traversal protection.
- Browser: 3 passed (authenticated fixtures, auth failure/mobile width, mock labelling).
- TypeScript and Vite production build passed.
- npm audit: zero known vulnerabilities in the resolved frontend dependency set.
- pip-audit: no known vulnerabilities in the pinned Python runtime dependencies.
- Base and NVIDIA-overlay Docker Compose configuration validation passed.
- Multi-stage Linux image build passed.
- Container smoke: public static UI, security headers, API authentication, mock telemetry, disabled controls, UID 10001 and SQLite persistence across container recreation passed. Test containers and temporary volume removed afterward.
- One upstream Starlette/httpx deprecation warning occurs in backend tests. Container test cache may warn because /app is owned by root; tests still pass. These do not indicate a runtime failure.

Browser tests use explicit fixtures and container smoke uses mock telemetry. None of these constitute performance measurements or verification of the Ubuntu server.

## Pending host acceptance

No SSH connection, deployment, merge or host modification was performed. Ubuntu 24.04 real CPU/RAM readings, GTX 950/P100 NVIDIA telemetry, existing Container Toolkit support, target IP/port availability and reboot persistence need manual acceptance using INSTALL.md after review. Netdata, SSH, drivers, CUDA and existing native engines were untouched.

Container engine status is deliberately unavailable until a separate safe host integration is reviewed. Existing engine upstreams, service names, launch arguments, endpoints and licenses are unverified. Model inventory defaults empty until a narrow read-only directory mount is configured by an administrator. Automated benchmark runs, engine/model operations and tuning are future phases.
