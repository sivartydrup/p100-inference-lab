# Native benchmark runner (disabled by default)

This is a host-administrator CLI for fixed `llama-bench` microbenchmarks, with authenticated **read-only** dashboard history. It is not a native server manager. No native job can be submitted or cancelled through HTTP; those routes always return 403. Base Compose does not start a native worker, mount native files, or configure native history. Windows remains development-only. Production UI/API and this optional worker all run on Ubuntu.

## Current support and acceptance gates

Only Kmic and qr0n presets using the reviewed `llama-bench-json-v1` protocol are accepted. On the inspected host, Kmic was revision `bbad6fcfbf224d4445a89150155c7502d03d3ac1` and qr0n's outer repository was `dc5c644c45f35167edfa041110f8c9bd75404b34`. The embedded qr0n benchmark reports its own build commit; pin the benchmark's reported full source revision in the preset, and separately retain the outer repository revision in the operator's build records. Registration is not proof of binary compatibility. Both installed CUDA builds targeted sm_60, whereas the inspected GTX 950 is compute 5.2. No suitable full model or P100 was available. **Do not enable these presets on that hardware.**

PXA remains inventory-only: its release-specific output protocol has not been accepted, and its separately licensed `libggml-pxqn.so` must not be bundled with this Apache-2.0 project. No engine, model, CUDA library or new Python/npm dependency is distributed by this change.

The implementation is tested with compiled Linux test programs and simulated hardware. Actual systemd user-unit policy enforcement, the installed engine output, cancellation/crash recovery and P100 measurements still require an isolated Ubuntu acceptance run after review. Do not interpret a passing unit test or CUDA metadata as proof that a real kernel used the GPU.

## Execution boundary

Use a non-root Linux account with a working systemd user manager and delegated cgroup memory/CPU/process controllers. Ubuntu 24.04's systemd 255 is the target. User namespace/mount hardening must be supported. Missing capabilities fail closed; do not remove properties to make a run start. Nothing installs/enables a service, changes user lingering, runs sudo, restarts Docker, or alters existing native services, SSH, Netdata, CUDA or drivers.

Three explicit gates apply: `LAB_ENABLE_NATIVE_RUNNER=true` in the host CLI environment, a registry with `enabled: true`, and a preset with `reviewed: true`. The shipped example has all execution disabled and contains no usable presets. The web token grants no native execution capability.

The registry is local administrator configuration, never browser input. It rejects unknown fields, duplicate keys, symlinks, unsafe ownership/writable modes, unbounded values and unsupported engines. Absolute executable/model/library paths and SHA-256 hashes are required. The executable must be named `llama-bench`; the runner builds a fixed argument vector without a shell. There are no arbitrary extra flags, plugins, downloads, server ports or inherited environment overrides. Treat the worker account, its Python installation, registry directory, engine installation and dependency loader paths as trusted. This is not a sandbox for hostile executables or malicious local users.

Each trial has a unique systemd user unit. Durable intent is committed before launch. A private start gate permits native exec only after the worker observes the requested unit limits and hardening. An expired gate cannot release a late-created unit. Native environment is an allowlist (PATH, LANG, CUDA_DEVICE_ORDER, CUDA_VISIBLE_DEVICES, OMP_NUM_THREADS, LD_LIBRARY_PATH); dashboard credentials and other parent secrets are excluded. The unit uses a bounded runtime, memory, CPU quota, process count, control-group termination, read-only system/home mounts, private temporary files and AF_UNIX-only socket creation. These properties are checked before releasing the gate. See [systemd-run](https://www.freedesktop.org/software/systemd/man/255/systemd-run.html), [termination policy](https://www.freedesktop.org/software/systemd/man/255/systemd.kill.html) and [resource controls](https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html).

Both streams are drained under a combined 256 KiB cap. Cancellation, shutdown and time limits stop only the recorded unit, then confirm inactive/failed or unloaded status. Unconfirmed cleanup records `cleanup_failed` and blocks further claims. The worker's next startup inventories its namespace, refuses unknown units, confirms cleanup and records interrupted work without replay. Inspect and resolve unexpected units locally before retrying; do not delete database records to bypass recovery. RuntimeMaxSec remains a final bound if the worker crashes. A systemd policy/configuration failure is a failed run, not permission to weaken isolation.

One OS lock per canonical database plus a shared per-user runtime lock exclude cooperating workers, including workers using different databases. Resource preflight pins GPU UUID, driver and compute capability, checks available VRAM/RAM/disk, temperature and existing GPU compute processes. **This is not exclusive GPU ownership against other accounts or unrelated tools.** Reserve the GPU operationally; do not run other inference jobs concurrently. The worker never stops existing native inference to make space. File hashes are checked before work; identity changes are checked between trials. Only explicitly listed libraries are pinned: the administrator must audit the full trusted dependency closure, loader configuration and build compatibility separately.

## Administrator setup (after review and host acceptance)

Install the Python backend into a private host virtual environment, using `backend/requirements.lock`; do not modify a native engine environment. Choose a new dedicated local SQLite path, never the existing dashboard/fixture database. An existing unrelated database is refused. Keep the parent directory private. For example, with operator-selected paths:

```bash
umask 077
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 init
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 list
```

Copy `deploy/native-presets.example.json` to a private configuration directory. Construct a preset against the strict `NativePreset` schema in `backend/lab/native_config.py`; inspect its schema with `python -c 'from lab.native_config import Registry; import json; print(json.dumps(Registry.model_json_schema(), indent=2))'`. Retain the exact reviewed manifest and build/license records. Every binary/library/model artifact needs `path` and `sha256`. Include all required library artifacts, model ID/license, engine revision, declared CUDA version, GPU UUID/capability/driver and resource bounds. Paths containing `$`, `%`, colons, line breaks or NUL are rejected. Set threads, prompt/output tokens, batch/microbatch, GPU layers, warmup/trial counts and per-trial/job deadlines explicitly. The declared CUDA version is operator metadata, not an automatically verified build fact.

Only after validating those facts and the host boundary, set the registry/preset gates and CLI opt-in. The following illustrates commands, not an instruction to enable the supplied empty registry:

```bash
export LAB_ENABLE_NATIVE_RUNNER=true
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 submit \
  --registry /PRIVATE/config/native-presets.json --preset REVIEWED_ID \
  --request-id "$(python -c 'import uuid; print(uuid.uuid4())')"
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 work \
  --registry /PRIVATE/config/native-presets.json --once
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 export JOB_UUID
python -m lab.native_cli --db /PRIVATE/native/history.sqlite3 cancel JOB_UUID
```

Omit `--once` for an explicitly supervised persistent worker. Registry changes require a worker restart; queued jobs whose recorded preset fingerprint differs from the loaded registry fail rather than silently adopting new settings. Ten queued/active jobs are allowed. UUID request IDs provide idempotency: reuse the same ID to resolve an uncertain submission. Local cancellation records intent; only a worker's terminal state and confirmed unit cleanup establish completion. SIGTERM requests graceful cleanup; after a hard crash, restart the same worker/database to recover. The CLI deliberately reports bounded error codes, not engine stderr or private paths.

## Read-only dashboard connection

`LAB_NATIVE_DB` optionally selects the separate native SQLite database. Authenticated GET collection/detail/export endpoints open it read-only, verify its application/schema identity, and never initialize or migrate it. Unconfigured history is empty; unavailable or incompatible configured storage returns 503. Native jobs remain separate from manual records and simulated fixture jobs. Browser submission and cancellation remain disabled even if the host environment opt-in is set.

For container deployment, an operator must explicitly arrange a **read-only directory mount** containing the database and read-only access for container UID 10001, including any SQLite recovery/journal requirements. Do not mount just a replaceable database inode, expose a writer permission, mount the systemd bus or Docker socket, or broaden permissions on engine/model directories. No ready-to-enable production overlay is shipped. Establish private group/ACL access deliberately and verify read-only concurrent reads on the chosen local filesystem before deployment. Keep the native database on a local filesystem, back it up only while idle or with SQLite's backup API, and protect exports as potentially sensitive machine/build metadata. There is no automatic retention or deletion.

## Measurement interpretation

Each invocation runs one repetition containing a prompt-processing row and a generation row. The parser requires strict finite JSON, the configured model/workload, matching build commit, CUDA backend metadata and internally consistent raw nanosecond/rate samples. It rejects additional/missing/duplicate rows, changed settings and malformed/nonzero runs. Stored output is whitelisted and strips the model path; raw stdout/stderr are hashed rather than retained as unrestricted text.

Measured repetitions report prefill/decode throughput and process wall time with median, minimum, maximum and population standard deviation. Warmups are stored but excluded from summaries. Each trial starts a fresh process and reloads the model: warmup can warm OS/GPU caches but does not create a resident-server warm session. Process wall time includes launch, model load and supervisor overhead. GPU values are snapshots before/after, **not peak VRAM or energy measurements**. No TTFT, answer quality, dataset/sampler reproducibility, continuous thermal limits or multi-GPU protocol is implemented. Partial failed/cancelled jobs retain samples without a successful aggregate. Compare equivalent pinned workloads only; do not present these as chat latency or P100 performance until actual hardware acceptance is complete.
