# Phase 2 foundation verification

Validated locally on 15-16 November 2026. The Ubuntu production deployment was not changed, and no native engine or model was launched.

- Windows backend: 60 passed, 2 skipped (symlink-permission limitations).
- Linux image backend: 62 passed, including both symlink tests.
- Browser: 12 passed, including telemetry/authentication, fixture permissions/retry/cancel/export/mobile, slow-detail timeout/recovery and selection/disconnect cleanup.
- TypeScript/Vite production build passed.
- npm audit reported zero known vulnerabilities; no new runtime dependencies were introduced. CI also audits the unchanged Python runtime lock.
- Base, NVIDIA-overlay and fixture-overlay Compose configurations validated.
- Linux multi-stage image build passed.
- Container smoke exercised a separately authorized API and actual non-root fixture worker on a private local Docker volume. Verified disabled-by-default behavior, viewer denial, queued cancellation, warmup/raw trial separation, success/failure/timeout, second-worker rejection and result persistence across API recreation. The worker runs without networking, GPU access or API/job credentials. Test containers and the temporary volume are removed by the test's cleanup.
- Hard-killed worker process coverage verifies kernel lock release and interrupted-job recovery. Migration coverage preserves existing Phase 1 manual history and refuses future schemas. Concurrent retries create only one job, and cancellation wins over a later successful completion.

An existing upstream Starlette/httpx TestClient deprecation warning remains; tests pass. Fixture durations/rates are synthetic protocol values, not inference timings. No benchmark was measured on the GTX 950 or P100, no model was downloaded and no engine library was redistributed. P100/model readiness, real engine execution isolation, native readiness protocols, measured telemetry capture and production rollout remain separate work.

## PR review fixes - 16 November 2026

- Worker lock filenames now derive entirely from the canonical resolved database path. A different-extension symlink cannot acquire another lock or recover a still-running job; regression coverage checks both lock acquisition orders.
- Job detail requests have an independent, non-overlapping polling lifecycle. List refreshes no longer abort slow detail responses. Timeouts display a warning and preserve visibly stale results; retries recover. Selection changes and disconnect explicitly abort obsolete requests and stop timers.
- Recovery coverage deliberately forces timestamp ties and both UUID orderings, then follows the actual claim result rather than assuming insertion order.

Frontend production build and all 12 browser tests pass locally. Windows backend: 60 passed, 2 symlink-permission skips. Linux backend: all 62 passed; the complete separate-worker container smoke passed. The review subagent independently confirmed all three fixes and found no further actionable issues in the focused delta. No deployment or server changes were made.
