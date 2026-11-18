# Measured-history verification — 18 November 2026

Validated locally and in disposable Linux containers. No production deployment, server data import, GPU run, driver/power change or boot-service change was performed. Real operator archives and preview snapshots remain outside the public repository; committed fixtures are synthetic and make no hardware-performance claim.

- Windows backend: 131 passed, 8 platform-specific skips. Linux backend with a C compiler: 139 passed, no skips. One existing Starlette/HTTPX deprecation warning remains.
- Chromium: 15 passed, including imported labels, cap-evidence distinction, partial/cutoff rendering, first-text semantics, authenticated export, absent execution controls and mobile width.
- TypeScript/Vite and Docker production-image builds passed.
- Existing container smoke passed authenticated API/persistence and the isolated fixture worker lifecycle.
- History container smoke passed offline import/idempotency, non-root API access to a read-only published snapshot, authenticated export, manual-history separation, disabled HTTP native mutations and actual write rejection without changing snapshot bytes.
- Base/GPU/history Compose combinations validate. The Linux Python runtime lockfile audit found no known vulnerabilities.
- Four private resident archives converted, crosschecked against raw evidence and imported idempotently into a new local native database. A separate snapshot rendered through the real local API and compiled UI. Completed runs and the thermal cutoff remained distinct; the initial process-per-trial benchmark is a different protocol and remains separately preserved.

Snapshot publication uses bounded backup progress and atomic replacement; imports/snapshots preserve unrelated data and never enqueue work. The web/fixture schema is unchanged. Actual production mount/ACL/deployment acceptance remains a later approved step. Import validation is not independent hardware attestation or acceptance of the separate managed systemd runner.

## Outstanding audit failure

`npm audit --audit-level=high` fails for existing Vite build dependencies: picomatch plus its fdir/tinyglobby/Vite chain. [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), updated 17 November 2026, lists picomatch through 4.0.7 and no patched release. The npm registry reports 4.0.7 as the latest release at verification time. This feature adds no dependency and does not change those locked versions.

No audit suppression or forced Vite major downgrade was applied. A separate failing frontend-audit CI job keeps the issue visible while functional frontend checks run. Production contains static assets plus Python, without the Node/Vite build toolchain; development/build exposure still needs review. Do not describe the frontend audit or all CI checks as passing. Merge/deployment should wait for remediation or an explicit reviewed policy decision.
