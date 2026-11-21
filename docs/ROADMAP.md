# Benchmark and tuning roadmap

The optional [fixed Kmic service agent](ENGINE_AGENT.md) adds live model/readiness
observation and a separately authenticated start/stop/boot-toggle boundary for
one pinned existing user unit. It is not installed by default and still requires
real host acceptance. This does not enable browser-selected models, arbitrary
engine commands, tuning or automatic benchmarking.

An optional [administrator-started worker](ADMIN_WORKER.md) now provides fixed non-root system-unit payloads, root-owned queue/preset boundaries, effective isolation checks, continuous sampled thermal/150 W policy checks and durable interruption recovery. It is uninstalled/disabled and requires actual integrated host acceptance. It adds no browser controls or scheduling permission grant.

Ubuntu native-worker acceptance exposed ineffective filesystem isolation despite reported user-unit properties. The child now verifies effective kernel restrictions before exec and refuses the current unsupported host boundary. A separately reviewed host-isolation solution and resumed acceptance remain prerequisites to enabling managed native benchmarking; see [NATIVE_VERIFICATION.md](NATIVE_VERIFICATION.md).

Multi-GPU session history now adds optional immutable participant/enclosure declarations, per-card conditions and conservative overlap checks. See [MEASURED_SESSIONS.md](MEASURED_SESSIONS.md). This continues Phase 2; it does not enable Phase 3 tuning or validate the native systemd worker's enforcement boundary.

Version 0.4 adds a local resident-log import protocol, immutable terminal history, read-only snapshot publication/overlay and measured comparison UI. It preserves partial thermal-cutoff results and distinguishes logged from declared power caps. This provides history for external resident acceptance runs; it does not complete native systemd execution acceptance, enable browser controls or constitute a production deployment. See [MEASURED_HISTORY.md](MEASURED_HISTORY.md).

## Phase 1 — current foundation

Unified observation, explicit service status, read-only inventory, manual benchmark records and disabled reserved control endpoints. Simulated telemetry is explicitly labelled; no synthetic benchmark results or implied P100 performance claims. Host verification is required before operating real engines.

## Phase 2 — reproducible measurements

Add reviewed engine-specific readiness and inference protocols after verifying actual installed Kmic, PXA and qr0n revisions. Record engine commit/build, CUDA/driver versions, GPU UUIDs/topology, model hash/license/quantization, exact fixed launch preset, context size, batch and microbatch, split strategy and tensor split. Track dataset hash, prompt/output token counts, seed, sampler, cache state, warmup, concurrency and workload type.

Separate time to first token, prefill tokens/sec, decode tokens/sec, wall-clock latency, peak VRAM and energy estimates. Repeat measured trials after warmup; report medians and variance. Compare equivalent token workloads and answer quality. Distinguish cold load, warmed inference and cached prompts. Store raw samples, failures and configuration snapshots, not only averages. Add cancellable job state, migration/versioning, retention, export and backup/restore tests before automated scheduling.

## Phase 3 — safe tuning

Versioned administrator-authored presets; validate engine flags against the pinned binary. Add GPU ownership and mutually exclusive service scheduling, resource preflight checks, bounded run duration and explicit interruption warnings. Sweep context, GPU split, batch and kernels under a fixed reproducible protocol. Never accept arbitrary shell flags or executable paths from browser users. Add regression comparisons and hold-out correctness checks before claiming a speed improvement.

## Phase 4 — custom engine experiments

Choose a baseline only after comparable measurements. Keep experimental kernels/forks separate from management code. Capture compiler/CUDA targets and Pascal-specific support; benchmark changes against a pinned baseline. Review upstream licenses before distributing forks or linking components. Add multiuser roles, HTTPS, secret rotation and a separate worker permission boundary before expanding beyond a trusted single-administrator trusted LAN/tunnel.

## Phase 2 foundation status

The fixture-only job lifecycle is implemented: durable queue, separate locked worker, bounded fixed presets, second job credential, idempotency, cancellation, timeout/failure/interruption, raw samples, warmup exclusion, statistics/provenance and export. See PHASE2.md. No synthetic fixture metric is a claim about P100 or GTX 950 performance.

The next increment implements a disabled host-only Kmic/qr0n native CLI microbenchmark boundary, separate database and read-only dashboard/export. See [native runner](NATIVE_RUNNER.md) and [verification](NATIVE_VERIFICATION.md). This does not complete Phase 2: actual Ubuntu systemd enforcement, compatible P100/model readiness and release-specific output acceptance remain prerequisites to enabling it. PXA protocol/licensing integration, resident-server TTFT, correctness datasets, peak-memory/energy sampling, equivalent-workload comparison and backup/restore acceptance remain future work. Remote management and Phase 3 tuning are not enabled.
