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

## Proposed time-limited audit acceptance

Raw npm audit still reports four high findings through existing picomatch/fdir/tinyglobby/Vite build dependencies. [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) lists picomatch through 4.0.7 and no patched release; the registry latest remains 4.0.7. No dependency version was changed or downgraded.

The proposed security-exception.json expires at 2026-11-25T00:00:00Z. npm run audit:security permits only this exact development-only advisory/version/path chain, preserves the raw findings and independently audits production dependencies at the high-severity threshold. Eight tests cover acceptance scope and rejection of new advisories, production exposure, changed versions/paths, critical escalation, invalid reports, cycles and expiry. CI uploads the unmodified JSON artifact. Local policy checks pass; the production audit reports zero vulnerabilities.

This is explicit risk acceptance proposed for owner review, not an upstream fix or a clean full audit. The PR stays in draft pending review. Production lacks the Node toolchain; development/build exposure remains. Expiry is not automatically renewed; no merge or deployment is automatic.
