# Benchmark and tuning roadmap

## Phase 1 — current foundation

Unified observation, explicit service status, read-only inventory, manual benchmark records and opt-in fixed service controls. No synthetic results or implied P100 performance claims. Host verification is required before operating real engines.

## Phase 2 — reproducible measurements

Add reviewed engine-specific readiness and inference protocols after verifying actual installed Kmic, PXA and qr0n revisions. Record engine commit/build, CUDA/driver versions, GPU UUIDs/topology, model hash/license/quantization, exact fixed launch preset, context size, batch and microbatch, split strategy and tensor split. Track dataset hash, prompt/output token counts, seed, sampler, cache state, warmup, concurrency and workload type.

Separate time to first token, prefill tokens/sec, decode tokens/sec, wall-clock latency, peak VRAM and energy estimates. Repeat measured trials after warmup; report medians and variance. Compare equivalent token workloads and answer quality. Distinguish cold load, warmed inference and cached prompts. Store raw samples, failures and configuration snapshots, not only averages. Add cancellable job state, migration/versioning, retention, export and backup/restore tests before automated scheduling.

## Phase 3 — safe tuning

Versioned administrator-authored presets; validate engine flags against the pinned binary. Add GPU ownership and mutually exclusive service scheduling, resource preflight checks, bounded run duration and explicit interruption warnings. Sweep context, GPU split, batch and kernels under a fixed reproducible protocol. Never accept arbitrary shell flags or executable paths from browser users. Add regression comparisons and hold-out correctness checks before claiming a speed improvement.

## Phase 4 — custom engine experiments

Choose a baseline only after comparable measurements. Keep experimental kernels/forks separate from management code. Capture compiler/CUDA targets and Pascal-specific support; benchmark changes against a pinned baseline. Review upstream licenses before distributing forks or linking components. Add multiuser roles, HTTPS, secret rotation and a separate worker permission boundary before expanding beyond a trusted single-administrator tunnel.
