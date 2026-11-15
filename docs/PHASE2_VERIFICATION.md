# Phase 2 foundation verification

Validated locally on 15 November 2026. The Ubuntu production deployment was not changed, and no native engine or model was launched.

- Windows backend: 59 passed, 1 skipped (existing symlink-permission limitation).
- Linux image backend: 60 passed, including the symlink test.
- Browser: 10 passed, including the seven existing telemetry/authentication cases and three fixture UI permission, retry/cancel/export and mobile cases.
- TypeScript/Vite production build passed.
- npm audit reported zero known vulnerabilities; no new runtime dependencies were introduced. CI also audits the unchanged Python runtime lock.
- Base, NVIDIA-overlay and fixture-overlay Compose configurations validated.
- Linux multi-stage image build passed.
- Container smoke exercised a separately authorized API and actual non-root fixture worker on a private local Docker volume. Verified disabled-by-default behavior, viewer denial, queued cancellation, warmup/raw trial separation, success/failure/timeout, second-worker rejection and result persistence across API recreation. The worker runs without networking, GPU access or API/job credentials. Test containers and the temporary volume are removed by the test's cleanup.
- Hard-killed worker process coverage verifies kernel lock release and interrupted-job recovery. Migration coverage preserves existing Phase 1 manual history and refuses future schemas. Concurrent retries create only one job, and cancellation wins over a later successful completion.

An existing upstream Starlette/httpx TestClient deprecation warning remains; tests pass. Fixture durations/rates are synthetic protocol values, not inference timings. No benchmark was measured on the GTX 950 or P100, no model was downloaded and no engine library was redistributed. P100/model readiness, real engine execution isolation, native readiness protocols, measured telemetry capture and production rollout remain separate work.
