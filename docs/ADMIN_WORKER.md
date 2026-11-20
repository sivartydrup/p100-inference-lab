# Administrator-started native worker

This optional transport uses temporary **system** units, started by a manually invoked root controller, with each engine running as an explicitly selected existing **non-root** account. It reuses the native queue, strict presets/parser, approval guard, unit cleanup, recovery and durable samples. It does not install a daemon, grant sudo/polkit permissions, change AppArmor/sysctls, or add an HTTP execution API. Nothing in Compose starts it. Browser native mutations remain disabled.

The user-unit transport remains available but must refuse execution when kernel isolation cannot be established. A shared-executor AppArmor exception is not required for this transport: the administrator initiates each bounded system unit, and the engine receives no root identity or capabilities. Administrator-started CPU and GPU experiments on Ubuntu established this approach's feasibility; they are distinct from acceptance of this new queue-integrated transport.

## Trust and installation boundary

Before enabling this transport, install the backend and locked dependencies into a **new root-owned runtime** outside user-writable directories, such as `/opt/p100-lab-worker/venv`. Use isolated Python (`-I`). The controller checks root ownership, file modes, parent paths, package/dependency trees and interpreter before importing the worker implementation. The administrator must establish trust in the installer/runtime itself before running Python as root; these checks cannot retroactively make executing an untrusted Python installation safe.

The database directory must already exist, be root-owned/private (0700), and have no writable or symlinked ancestors. The database and its `.admin.json` identity marker are root-private (0600). `init` requires a new database; existing user-runner/import/history databases are not adopted. The marker binds the payload UID/GID on first worker startup under the global lock. Changing that account requires a new database, not editing the marker. Malformed/missing markers refuse operation.

Registry files, executable, model and listed libraries, including their parent directories, must be sealed under root ownership without group/other write permissions. Artifact paths cannot be symlinks. Existing group-writable native installations should be copied into a separately reviewed sealed artifact directory with byte hashes verified; do not change native installation permissions to satisfy this transport. The payload account needs read/traverse access to these copies and to the runtime/guard, plus execute access to the approved binary. Preserve all upstream engine/model/library licenses. Nothing downloads or copies assets automatically.

The operator remains responsible for verifying the full native loader/dependency closure, compatible CUDA build and approved model. Preset SHA-256 checks pin only explicitly listed artifacts. An isolated unit does not make an untrusted engine or model safe; local users/root outside this boundary are not adversarially isolated from the GPU driver. Operationally reserve the selected GPU and do not run unrelated tools concurrently.

## Commands after review and host acceptance

The examples assume paths and accounts have already been deliberately prepared. They do not install any permissions or services. Use a reviewed registry matching `NativePreset`, with `enabled: true`, `reviewed: true`, and `max_temperature_c` at or below 75. The same strict flags, bounds and parser as the original CLI apply. PXA remains inventory-only.

```sh
sudo /opt/p100-lab-worker/venv/bin/python -I -m lab.admin_cli \
  --db /var/lib/p100-lab-worker/native.sqlite3 init

sudo env LAB_ENABLE_ADMIN_RUNNER=true /opt/p100-lab-worker/venv/bin/python -I -m lab.admin_cli \
  --db /var/lib/p100-lab-worker/native.sqlite3 submit \
  --registry /etc/p100-lab-worker/presets.json --preset REVIEWED_ID \
  --request-id 11111111-1111-4111-8111-111111111111

sudo env LAB_ENABLE_ADMIN_RUNNER=true /opt/p100-lab-worker/venv/bin/python -I -m lab.admin_cli \
  --db /var/lib/p100-lab-worker/native.sqlite3 work \
  --registry /etc/p100-lab-worker/presets.json --user trav --once
```

Choose a new UUID for a new request; reuse it to resolve uncertain submission. No arbitrary command, flag list or payload script is accepted. `init`, `list`, `export JOB_UUID` and `cancel JOB_UUID` need the trusted root runtime/database but do not enable execution. `submit` and `work` additionally require `LAB_ENABLE_ADMIN_RUNNER=true` and both registry/preset gates. Omit `--once` only when deliberately supervising the foreground controller; no service unit or auto-start mechanism is provided.

## Enforcement and recording

The systemd command fixes numeric User/Group to the non-root account, drops the entire capability bounding set and requests control-group kill, runtime/memory/CPU/task limits, PrivateUsers/PrivateTmp, strict read-only system/home, NoNewPrivileges and AF_UNIX-only sockets. A private, isolated Python guard must independently verify a non-root effective UID, empty effective/permitted/bounding capabilities, effective namespaces, seccomp, read-only artifact mounts and denied IPv4/IPv6 socket creation before exec. The worker also attests the non-root identity and empty capabilities. Python `-I` prevents payload account startup hooks from preceding the guard.

Approval directories are root-owned with group traversal only (0710); the payload cannot create an approval file. The controller publishes the group-readable, root-owned approval atomically (0640) after checks. The guard pins the worker's kernel start time as well as its pidfd, refusing a recycled PID rather than accepting a stale approval after a hard crash. These directories are short-lived under `/run`, outside the private root global-lock directory. A hard-killed controller may leave its root-owned evidence/gate directory until reboot or deliberate operator cleanup; it grants no execution authority to a recycled worker PID. No dashboard/job credentials are inherited by the native environment.

Use a dedicated non-administrative payload account where practical. Normal account group/IPC access still applies; AF_UNIX sockets are allowed for local runtime needs. This does not turn an existing account into a hostile-code sandbox.

During supervised execution, a bounded read-only NVIDIA query checks the selected GPU's temperature and **requested and enforced 150 W limits** approximately every 0.5 seconds. Unavailable/malformed telemetry, changed caps, reaching the preset cutoff (maximum 75 C), cancellation and deadlines stop the recorded unit and require cleanup confirmation. This is a sampled software cutoff, not an instantaneous hardware interlock. GPU power settings are never changed, and other processes/services are not stopped to make room.

`admin-systemd-v1` and the cap/cutoff policy are recorded in job provenance. Existing schema, model/build/artifact/workload provenance, raw warmup/measured samples, measured wall times, warmup exclusion, summaries and JSON export remain available. Process wall time is measured by capture, not inferred from engine timings. Partial/interrupted jobs keep committed samples and events without a successful aggregate. This process-per-trial protocol differs from resident chat inference.

The root global lock excludes cooperating administrator workers across databases. The per-database lock and payload binding are checked before recovery/claim. Root and user managers have separate unit namespaces; only this system manager's `p100-lab-bench-…` units are considered. Unknown units cause refusal rather than being stopped. Committed intent precedes unit creation. Restart cleans known recorded units, marks interrupted jobs and never replays them. RuntimeMaxSec bounds a unit after a hard controller crash. Unconfirmed cleanup blocks subsequent claims.

## Deployment status and rollback

This transport ships disabled and uninstalled. Tests simulate transport/hardware except where explicitly noted; the real worker/system-manager crash/recovery sequence still requires manual Ubuntu acceptance in the root-owned installation. Do not interpret a synthetic JSON fixture as GPU performance. The earlier one-off isolation/GPU experiments did not deploy this worker.

No production changes are required to review the PR. For a later approved installation, stop the foreground controller with SIGTERM, wait for confirmed unit cleanup and retain the private database/artifacts for diagnosis. If the controller crashed, invoke recovery using the same database/account before any further jobs. Do not erase records/markers, alter preset hashes, stop wildcard namespaces or weaken checks to bypass a refusal. A read-only published dashboard snapshot remains a separate administrator-reviewed operation; do not mount the live root writer or service bus into the API container.
