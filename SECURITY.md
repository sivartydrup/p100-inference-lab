# Security and planned control boundary

All `/api/*` routes require a randomly generated bearer token (at least 32 characters). `/healthz` and static UI assets are public. Authentication uses constant-time comparison; no browser cookies or CORS are enabled. Token stays only in React memory and clears on reload/disconnect. API responses disable caching. Never put credentials in URLs, source control, browser storage or frontend build variables. Rotate the token file and recreate the container; open tabs must reconnect with the new token.

The requested http://192.168.0.188:3000 endpoint is for a trusted isolated LAN only. HTTP does not encrypt credentials or history. Use the SSH tunnel or an HTTPS gateway described in INSTALL for untrusted networks. There is no internet exposure configured. Authentication is mandatory even on the LAN. A shared token currently permits history writes as well as reads; it has no host management capability. Rate limiting, individual users and roles are future work.

## Phase 1 boundary

No engine/model control is implemented. The reserved typed control route returns 403 even if LAB_ENABLE_CONTROLS is set. No process startup on app boot, downloads, shell interpretation, arbitrary command/executable/URL/unit/preset input, Docker socket, sudo or host service bus mount exists. The only subprocess calls are fixed argument arrays for read-only nvidia-smi and native user-service status. Native services remain outside Docker; Netdata and SSH are independent.

Container runs UID 10001 with no capabilities, no-new-privileges, read-only root and writable data/tmp only. Host proc read-only mount and optional NVIDIA utility devices expose host metadata, so a compromised container remains a risk; this is not a sandbox for untrusted code. Container secret files are local bind mounts, not an encrypted secret vault. Trusted local accounts/filesystems required; inventory symlink avoidance is not protection against concurrent malicious path replacement.

## Proposed narrow control layer (not implemented)

Before adding controls, verify actual Kmic/PXA/qr0n sources, licenses, binaries, launch commands and existing process ownership. Keep the web API unprivileged and separate from a dedicated native host agent. The host agent accepts a versioned schema over a local Unix socket with restrictive permissions; no LAN listener. A per-operation capability and authenticated, authorized administrator request are mandatory; do not reuse a read-only viewer credential.

Allow only a finite set of operations (start/stop, load/unload) against administrator-authored engine IDs, fixed service units and immutable preset/model IDs. Resolve IDs server-side to verified absolute paths; never accept shell strings, arbitrary flags, paths, service names or URLs. Use a single job queue, durable audit intent/outcomes, timeouts, idempotency and observed completion. Enforce GPU ownership/memory preflight, mutually exclusive engine scheduling and cancellation semantics. Require explicit user confirmation before interrupting active inference. Audit authenticated actor, preset hash, request ID and result, without credentials or prompts.

Ship only after HTTPS or a protected tunnel, separated read/control permissions, replay/CSRF review, credential rotation, audit integrity, host agent permission isolation and negative security tests are in place. Model downloads need a separate license/provenance and integrity design. No general shell endpoint at any phase.

Report suspected vulnerabilities privately to the repository owner through their published contact. Do not post secrets, private filesystem paths or working exploits in public issues.
