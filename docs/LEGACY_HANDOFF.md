# Archived pre-implementation handoff

Historical notes preserved from the local scaffold. They do not describe the current implementation; README, INSTALL and SECURITY supersede them.

# P100 Inference Lab — Phase 1 handoff

Created 15 November 2026 at:
C:\coding\sivartydrup\p100-inference-lab

The destination was inspected and empty before creation. No existing user files were overwritten. No remote deployment, GitHub repository creation, publication, SSH configuration changes, or Netdata changes were performed.

## Delivered

- React/TypeScript dashboard with CPU, RAM, disk and NVIDIA GPU monitoring.
- Python/FastAPI backend with mandatory bearer authentication on every API endpoint.
- Fixed native Ubuntu user-service adapters for Kmic, PXA and qr0n. Status is service activity, not inference readiness. Exact existing engine commands remain to be verified on the host.
- Engine start/stop controls disabled by default, with strict input validation, fixed service names, argument-array process calls, timeouts, operation serialization and SQLite audit records.
- Bounded read-only model inventory and SQLite manual benchmark history.
- Installation guide for trav@192.168.0.188 using a loopback backend and SSH tunnel.
- Architecture/API notes, security guidance, benchmark/tuning roadmap, contribution guidance, CI and service templates.
- Proposed MIT text in LICENSE.proposed; approval is required before making it the project license.

## Verification

- Backend: 22 passed, 1 skipped. The skipped test requires symlink creation privileges unavailable on this Windows host. Ubuntu CI includes that test.
- One upstream Starlette TestClient deprecation warning about httpx; no failed checks.
- Browser: 2 passed, using explicit fixture data, covering connection/authentication errors, monitoring display, disabled controls, browser storage avoidance, disconnect token clearing and mobile width.
- Production TypeScript/Vite build passed.
- npm audit reported zero vulnerabilities at verification time.
- Production static serving and API security/cache headers verified with the backend test client.
- Rendered desktop dashboard visually reviewed. The accompanying preview uses test fixture data, not measurements from the Ubuntu server.

## Next steps

1. Read README.md and docs\INSTALL.md in the project directory.
2. Verify existing engine binaries, launch commands, model roots and ports on Ubuntu before configuring any lab service.
3. Deploy the backend and built dashboard as documented, keeping controls disabled for initial host acceptance.
4. Approve MIT or request Apache-2.0. Before any publication, separately confirm the repository name and visibility.

Automated benchmarking, model loading/switching, engine readiness probes, GPU scheduling and tuning are future work, not Phase 1 capabilities.
