# Native runner verification

## Ubuntu enforcement acceptance, 20 November 2026

A real non-root systemd 255 user-unit probe ran a harmless private compiled CPU program, with no native engine/model/GPU execution. The original property gate accepted a unit whose requested read-only filesystem protection was ineffective: a test program created an empty file in its own private test directory. Cleanup was confirmed. This invalidates treating the older property-only unit test as host enforcement acceptance.

The correction requests/attests PrivateUsers and independently checks effective user/mount namespaces, NoNewPrivs, seccomp, read-only executable/model/library mounts and denied IPv4 socket creation in the child before native exec. Regression tests cover each missing guarantee, including a valid approval file outside isolation. Reference namespace identities are captured by the worker rather than requiring cross-user-namespace ptrace access.

The reviewed private code copy was tested on Ubuntu: host policy still prevented the required mount isolation, the child returned `Native isolation not confirmed`, the harmless executable's marker was absent, and no transient benchmark units remained. Kernel logs recorded AppArmor `unprivileged_userns` capability denials. No global host configuration or production installation was changed. A reusable manual CPU-only probe is provided at `tests/native_systemd_probe.py`.

This is successful refusal on an unsupported boundary, **not** completed native execution acceptance. Cancellation/descendant cleanup, worker crash/recovery, limits under effective isolation and the real P100 microbenchmark must be accepted after a separately reviewed host-isolation solution. Existing independent resident GPU measurements remain valid for their separate harness; they do not validate this worker.

Correction validation: 160 backend tests passed on Linux; 152 passed on Windows with 8 platform skips. The production Docker build passed. No runtime dependency or HTTP route was added. The inherited Starlette/httpx deprecation warning remains.

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

## Imported resident history (0.4)

External resident-model acceptance logs can now be validated and imported as terminal history; see MEASURED_HISTORY.md. Hardware/model readiness for those external measurements is recorded in their private evidence, and the history UI distinguishes it from managed CLI trials. These imports do not validate this worker’s actual systemd policy gate or authorize its execution. The original 0.3 test record above remains a record of that implementation stage. The new optional overlay mounts only a published read-only snapshot and is not enabled by default.
