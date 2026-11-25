# Host engine controls

The optional host agent observes and controls fixed systemd user units in
the existing engine account's user manager. It supports Kmic and optional
units for Kmic MoE, Strata, Scream P100, Inkling 2 Mini, PXA and qr0n. The base dashboard
starts in read-only mode.

Kmic supports an optional [host catalog and next-start configuration](MODEL_LIBRARY.md).
Other engines can publish their [fixed service profile](SCREAM_P100.md)
in the Model library.

Read-only host inspection on 21 November 2026 found this service active and
enabled, with its authenticated model endpoint reporting `qwen3.8-27b-p100` and
the configured model file `Qwen3.8-27B-UD-Q6_K.gguf`. The actual service differs
from the original inventory placeholder `p100-lab-kmic.service`. GPU inference
was active during inspection; nothing was stopped, restarted or reconfigured.

## Status and actions

The agent queries service state and boot enablement with fixed systemctl argv.
Health and model metadata come from a fixed administrator-configured local IPv4
endpoint using the existing native API key. Redirects/proxies are disabled,
responses are bounded, model paths are reduced to basenames, and raw service
output, credentials and prompts never enter the dashboard. A configured model
label is distinct from the server-reported loaded model.

Kmic supports start, stop and boot enablement changes. The optional
units support start and stop. Start/stop submit
nonblocking systemd jobs: acceptance is not completion or model readiness. Boot
changes do not start/stop the current process. Stop confirmation warns about
interrupting inference. Readiness, model and service state are shown separately.
Status uses the existing five-second inventory poll and refreshes after actions.

Before each mutation the agent verifies the unit-file digest, fragment path and
absence of drop-ins. Editing the unit requires separate operator review and a
new pinned digest. Boot symlink changes do not alter it. Starting an inactive
service requires two GPUs with at least 14,000 MiB free each, utilization below
10% and temperature below 75 C. The host agent checks peers for pending
jobs and uses one control lock across the configured units. Do not run independent
launchers or native benchmarks concurrently; they can race sampled preflight.
Existing unit settings govern the running engine. GPU/fan/power/driver/benchmark
policies are not changed.

## Privilege and credential boundary

The agent runs as the existing non-root engine UID with API-group GID 10001 and
no capabilities or sudo/polkit grant. Only it accesses its user's service manager.
Docker receives no host systemd bus, Docker socket, native engine/model paths or
upstream API key. The API receives only a read-only mount of the agent runtime
directory and two specific secret files. Unix sockets remain connectable through
read-only mounts; socket permissions and the IPC key authenticate the connection.

Credentials have separate roles:

- Existing API bearer token: observation/history access.
- Private IPC token: API-to-agent authentication, never browser-visible.
- Engine control token: browser memory only; sent in `X-Lab-Engine-Token` for
  mutations in addition to the API bearer token.
- Existing native model-server key: stays on the host and is not mounted in Docker.

The UI Unlock button arms local controls; the server validates the key on each
action. Explicit locking or disconnect/unmount clears it. Tokens never enter
URLs or browser storage. Trusted-LAN HTTP retains existing transport limitations;
use the project's SSH/HTTPS guidance for untrusted networks.

Requests have bounded size/time, a closed schema and a fixed operation allowlist.
The agent allows eight connections and refuses commands while busy, rather than
queuing delayed controls. It accepts no browser-provided unit, URL, model path,
executable, shell text or flags. Authorized API attempts use the existing audit
table without credentials. Transport errors/timeouts report unknown/refused
outcomes; mutations are not retried automatically. Accepted systemd jobs may
complete after disconnect. Refresh status before retrying.

Root and the engine account remain trusted. The native engine already runs as
that user, so this is not isolation against its compromise. A compromised API
holding the private IPC key can invoke enabled fixed actions; the host agent
deliberately offers no general management channel.

## Installation and acceptance boundary

`deploy/engine-agent/` contains examples, not an installer. Before installation,
review/pin the current unit, seal the agent runtime/configuration as root-owned,
verify UID/GID and user-manager boot support, and generate distinct random IPC
and control tokens. Secret directories should be 0750 root:10001 and secrets
0640 root:10001. The agent runs as trav/group 10001; the upstream key stays
owned/readable by trav. No native key is copied into Docker.

The service example creates `/run/p100-lab-engine-agent` with owner/group matching
the agent, mode 0750 and a socket mode 0660. Never make it world-writable or
reuse an unrelated path. RuntimeDirectoryPreserve=yes keeps the directory inode
stable across agent restarts so the API bind mount remains usable. The agent
uses an exclusive lock and refuses foreign files or active listeners; only a
same-owner socket confirmed connection-refused is removed as stale. If an
operator replaces the directory itself, recreate the API mount. Verify this
restart behaviour during host acceptance.

After review and acceptance, `compose.engines.yaml` configures observation.
Mutation also requires `LAB_ENABLE_ENGINE_CONTROL=true` and the distinct control
token. The old generic LAB_ENABLE_CONTROLS flag remains ineffective. Agent failure
leaves engine boot behaviour untouched and reports state/model unknown. Starting
the dashboard does not start the engine.

Required real-host acceptance remains: non-root sealed runtime, actual socket
access from the API UID, metadata authentication without key leaks, unit/drop-in
refusal, agent restart/API reconnect, audit persistence, and separately scheduled
start/stop/boot-toggle tests that restore original boot state. Do not interrupt
the running engine without explicit operator timing approval. Installation must
not change its boot setting. Restore the prior dashboard configuration if
integration fails. No automatic merge, install or deployment is configured.

Automated tests cover fixed argv, identity changes, invalid actions, GPU preflight,
busy refusal, authenticated Unix transport, API dual authorization/audit, UI
confirmations and stale-data controls. Container acceptance uses a synthetic
engine with real HTTP/Unix transport; it is not real systemd/GPU acceptance.

References: https://www.freedesktop.org/software/systemd/man/latest/systemctl.html
and https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
