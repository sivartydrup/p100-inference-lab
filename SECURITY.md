# Security

Phase 1 is a single-administrator application accessed through an SSH tunnel. A bearer token of at least 32 characters is mandatory at startup and on every API route. Generate it randomly; length alone does not provide entropy. Tokens are not stored in browser storage, URLs or audit records. Use one backend worker and loopback binding. Do not commit private environment files or databases.

Controls default off. Enabling them permits only start/stop for three hard-coded systemd user units. The application provides no remote command execution API, sudo integration or service file editor. Treat the token as full lab administrator access, including manual benchmark writes. Rotate by replacing the private environment value and restarting the backend. Disconnect clears the tab's token; an already-running server request may still finish.

No CORS is enabled; no ambient authentication cookie is used. Static dashboard assets and minimal liveness are public on the loopback listener. Access tokens do not protect unencrypted traffic on a publicly exposed HTTP listener. Keep the tunnel topology. Never put the token into a frontend build variable.

Local account access, browser extensions, malicious model-root filesystem mutation and compromised dependencies are outside this MVP's isolation boundary. Inventory follows no symlinks during normal traversal, caps results and exposes only relative names, but is not a security sandbox against concurrent local attackers. Engine units must have fixed trusted executable arguments and loopback listeners. A timeout does not guarantee cancellation of a systemd job.

Report suspected issues privately to the repository owner's contact once published. Until then, contact the project owner directly; do not post tokens, private paths or exploitable host details in a public issue.
