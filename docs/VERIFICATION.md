# Phase 1 verification - 15 November 2026

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

Browser tests use explicit fixtures and container smoke uses mock telemetry. These initial local checks do not constitute performance measurements. Subsequent Ubuntu validation is recorded below.

## Ubuntu host validation

Tested commit b0f696f on the Ubuntu 24.04 server on 15 November 2026. The administrator ran an isolated sudo test runner; results were read back over SSH. This was validation, not production deployment.

- Docker image built on the server, including the TypeScript/Vite dashboard build.
- Both Compose configurations validated.
- Backend container: 27 passed, with one upstream Starlette/httpx deprecation warning.
- Container smoke passed: static UI/security headers, mandatory API authentication, disabled engine controls, mock telemetry, UID 10001 and SQLite persistence across container recreation.
- Live CPU/RAM query succeeded through the read-only host proc mount. Reported memory total 33,481,232,384 bytes matches host MemTotal of 32,696,516 KiB. CPU was 0% in that short idle sample; this is not a load or accuracy benchmark.
- GPU query inside the container returned unavailable. Host nvidia-smi independently detected NVIDIA GeForce GTX 950 with 2048 MiB. NVIDIA container runtime/CLI were not found in PATH and the toolkit package query returned no installed version. Container GPU integration remains unverified pending separately approved toolkit setup.
- Netdata processes remained running. No SSH, Netdata, driver, CUDA or native-engine configuration was changed. No app was deployed or PR merged.

The runner completed successfully and includes removal of its test containers/volume and unique test image. Independent post-run Docker inspection was blocked by the account's sudo requirement, so cleanup was not independently confirmed. Test source and validation.log remain in the private temporary validation directory for review. Docker build cache/base images may remain.

## Remaining host acceptance

P100 hardware, production target URL, boot persistence and engine integration still require the acceptance procedure in INSTALL.md. The first validation did not install toolkit components; the authorized follow-up below added container tooling only. The one-shot live telemetry check used a temporary filesystem for its disk sample; production disk telemetry still needs comparison against the actual persistent volume.

Container engine status is deliberately unavailable until a separate safe host integration is reviewed. Existing engine upstreams, service names, launch arguments, endpoints and licenses are unverified. Model inventory defaults empty until a narrow read-only directory mount is configured by an administrator. Automated benchmark runs, engine/model operations and tuning are future phases.

## GPU blocker resolved - authorized follow-up

The administrator installed NVIDIA Container Toolkit 1.20.1-1 (four new container-toolkit packages, zero upgrades/removals). Driver 580.178.04, GPU identity and the default Docker runtime were unchanged. The package installation also added NVIDIA CDI discovery services. No driver/CUDA installation, engine launch or app deployment occurred.

The first activation attempt used Docker's `--gpus all` selector and failed despite successful runtime registration by reload. Its rollback restored the preceding daemon configuration. The second attempt registered the optional NVIDIA runtime again, reloaded Docker without restart and selected it directly with `--runtime=nvidia`, NVIDIA_VISIBLE_DEVICES=all and utility-only driver capabilities. This is now the repository's GPU Compose overlay configuration.

Results on Ubuntu:

- GPU query succeeded both directly in a utility container and through the application's live telemetry function running as UID 10001, with read-only root, no capabilities and no-new-privileges.
- Detected NVIDIA GeForce GTX 950, 2048 MiB total VRAM; observed 32 C, 24.68 W, 0% utilization in the idle sample. These are health readings, not inference performance results.
- Backend: 27 passed; container smoke passed again, including authentication and persistent SQLite.
- Before/after checks matched running-container IDs, PIDs, start times, restart counts and running states. GPU UUID and driver version matched. Netdata remained running.
- Post-run inspection found no temporary smoke containers/volumes or the unique validation image. Downloaded base images/build cache and the private test directory/logs may remain.

GPU telemetry is unblocked for the existing GTX 950. P100/CUDA workload validation remains future host acceptance. The application has not been deployed and the PR has not been merged.

## Review finding addressed - stale telemetry

Added a 10-second snapshot deadline with AbortController cancellation, five-second retries after completion/failure, 15-second sample expiry and tab-resume age checks. The UI retains the previous readings visibly marked stale and restores LIVE only after a fresh successful snapshot. Disconnect aborts pending requests and removes polling/age timers.

Production frontend build and all six browser tests pass locally. Added browser-clock regression scenarios for a held request timing out and recovering, sample age expiry after tab suspension, and disconnect during a held request without retries or retained credentials. Existing authentication, mobile layout and mock-labelling tests continue to pass. No server deployment or host changes were performed for this frontend fix.
