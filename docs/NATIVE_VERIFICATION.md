# Native runner verification

Implementation validation on 2026-11-16. This is a software test record, not Ubuntu/P100 deployment acceptance. Native execution ships disabled. No production container, host service, installed engine, driver, CUDA installation, Netdata configuration or SSH configuration was changed.

## Completed checks

- Windows Python suite: 100 passed, 8 platform-specific skips. One existing Starlette/HTTPX deprecation warning remains.
- Linux Python suite in a disposable Docker container with a C compiler: 108 passed, no skips. Tiny compiled ELF programs and dummy GGUF markers replace engines/weights; GPU telemetry is simulated. No NVIDIA devices or native host mounts were granted.
- Playwright Chromium: all 14 tests passed, including authenticated native history/export, read-only controls and mobile width; three repeated runs also passed (42/42). An initial CI run exposed a broad telemetry-warning selector matching the new panel's independent warning; the assertion is now scoped to telemetry.
- TypeScript/Vite production build passed; npm audit reported zero vulnerabilities and pip-audit found no known vulnerabilities in the Python runtime lockfile.
- Production Docker image built locally. Container smoke passed authenticated API/default-disabled native routes, persistence and the existing isolated fixture lifecycle. It launches no native engine.
- Base, GPU and fixture Compose configurations validate. CI additionally requires a C compiler so Linux ELF tests cannot silently disappear due to a missing compiler.

Native tests cover opt-in/strict presets, changed artifact hashes/modes, separate database identity and refusal to overwrite existing stores, idempotency/capacity/exclusion, immutable presets, raw sample validation, warmup statistics, malformed/nonzero/timeout runs, cancellation/shutdown, cleanup failure barriers, recovery without replay, unknown-unit refusal, fixed systemd policy/argv, approval-gate denial, real bounded subprocess IO and process-group descendant termination. API tests confirm missing history is not created, reads preserve database bytes, and host opt-in never enables HTTP mutations.

## Still required before enabling execution

1. Review the implementation and administrator manifest; independently verify exact native binary/reported build revision, full dependency closure, model license/hash and CUDA architecture compatibility. PXA is not supported for execution.
2. Isolated Ubuntu 24.04 acceptance under the intended non-root account: actual systemd user manager/cgroup controllers, namespace hardening, attested limits, denied-policy gate, timeout/cancellation and complete descendant cleanup, worker crash/restart and unknown-unit refusal. Current Linux tests exercise real subprocesses but simulate the systemd transport.
3. Install/verify compatible P100 hardware and an approved full model. The last inspected GTX 950 cannot run the installed sm_60-only Kmic/qr0n CUDA builds. This change does not rebuild or run them.
4. Verify any deliberate read-only native database mount/ACL and concurrent SQLite reads, backup/restore and operational GPU reservation. No such mount, service or production overlay is enabled by this PR.

Only then collect actual native performance. TTFT, correctness, peak VRAM and energy remain outside this microbenchmark protocol. Do not attribute synthetic test timing or fixture throughput to the installed hardware.
