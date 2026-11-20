# Administrator worker verification

## Ubuntu integrated acceptance: passed

On 20 November 2026 the administrator ran the reviewed `1fad279` acceptance
package on `p100-inference-server`. All five cases passed: durable synthetic CPU
success, cancellation, timeout, controller crash/recovery, and a real isolated GPU
job with durable results. Cancellation, timeout and crash reported cleanup.
The report is `/var/tmp/p100-admin-acceptance-p100-admin-acceptance-7002f870000f.json`;
evidence is `/var/lib/p100-admin-acceptance-7002f870000f`, with the retained sealed
runtime at `/opt/p100-lab-acceptance-1fad279-7002f870000f`.

The real GPU case stored two measured samples: median decode 189.42 tokens/s and
prefill 1062.83 tokens/s. This is a CLI token microbenchmark, not chat latency or
correctness evidence. No persistent worker service was installed. This acceptance
does not deploy the worker or publish its database into the dashboard. The
software-only findings below describe the earlier review boundary.

## Software verification before host acceptance

Software verification on 20 November 2026. The optional administrator transport is not installed, enabled or accepted as a persistent production worker by these checks.

- Linux backend: 189 passed, no skips. Windows: 180 passed, 9 platform skips. One inherited Starlette/httpx deprecation warning remains.
- Root transport command tests pin system-manager selection, numeric non-root User/Group, empty capabilities, isolated Python, fixed argv/environment and unchanged resource/isolation policy. Identity/capability mismatches prevent approval.
- Child regression tests reject root identity, nonempty effective/permitted/bounding capabilities, recycled worker identity and missing effective kernel protections. A valid approval file alone cannot establish permission to exec.
- Continuous-policy tests cover safe readings, rechecks during execution, thermal cutoff, requested/enforced cap changes and malformed/non-finite telemetry. Tests use simulated NVIDIA replies; they do not change GPU settings or establish sensor accuracy.
- A real Linux worker process was killed with SIGKILL after committing a synthetic warmup sample and the next trial's intent. SQLite retained both, kernel locks were released, and recovery marked the job interrupted without summary or replay. A second recovery did not repeat work. Systemd cleanup in this test is simulated; real system-manager recovery remains required.
- A disposable root container verifies the actual root-owned runtime/database boundaries, refusal of writable parent paths, new-database-only initialization, marker/account binding, disabled execution/registry/review gates, idempotent root submission and queued cancellation. It switches to the non-root lab account to prove the payload cannot forge a gate before approval but can read the root-published file afterward. No host bus, GPU or host native paths are granted.
- Existing durable-recording tests preserve process wall time on every stored trial, exclude warmup from aggregates and retain the administrator transport/policy provenance. Existing API/history/fixture behavior and read-only snapshot/export boundaries are covered by their container smoke checks.

The earlier administrator-started one-off CPU isolation and GPU/protocol experiments demonstrated the system-unit approach separately. They did not use this new queue-integrated controller, identity marker or gate implementation. Actual root-owned Ubuntu runtime installation and integrated success/cancellation/timeout, controller death before/after approval, unit-tree cleanup, unknown-unit refusal, restart/recovery and a short GPU trial remain explicit deployment acceptance steps after review. Neither unit tests nor synthetic CUDA-labelled protocol fixtures are P100 performance evidence.

No server runtime, data writer, dashboard image, model, native engine, Netdata, SSH, driver, CUDA, AppArmor, global sysctl, polkit, sudo policy or power/boot service was changed. No automatic install, merge or deployment is configured.
