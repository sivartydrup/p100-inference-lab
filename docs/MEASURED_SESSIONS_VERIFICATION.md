# Measured session verification

Validated on 19 November 2026. These checks concern software/history handling and do not certify a PSU or expand engine execution permissions.

- Windows backend: 142 passed, 8 platform-specific skips. Linux Python 3.12 backend with compiler: 150 passed, no skips. One inherited Starlette/httpx deprecation warning remains.
- Browser suite: 16 passed, covering paired measurements with different GPU UUIDs, overlap, missing/duplicate participants, workload mismatch, conflicting case declarations, partial outcomes, desktop/mobile width and GET-only network requests.
- TypeScript/Vite production build and Docker image build passed.
- Production container smoke passed authentication, persistent manual records and isolated fixture behavior. Native-history container smoke also passed optional context round-trip, immutable/idempotent import, non-root API, read-only snapshot, export and denied HTTP execution.
- Eight security-policy regression tests passed. Current npm full and production audits report zero vulnerabilities; the existing temporary exception was not used or extended. Python lockfile audit reports no known vulnerabilities. Audit results reflect the registry data at verification time, not a claim that an upstream advisory was fixed by this change.
- A private ten-record snapshot was rendered through the new built image's real authenticated API and compiled UI. It includes two two-card sessions, shows distinct per-card results and conditions, and passed desktop/mobile screenshot/overflow checks with no HTTP mutations. Private artifacts and identifiers are excluded from the repository.
- Legacy v1 digest compatibility and idempotent reimport were checked. Context changes conflict rather than overwrite an imported run. Per-GPU comparison keys remain distinct; session workload keys change with workload changes. Invalid participant/session/enclosure data is rejected.

No production image, snapshot, dashboard, native engine, Netdata, SSH, driver, CUDA, power policy or boot configuration was changed. Actual Ubuntu deployment remains a separate reviewed step. Native systemd execution enforcement acceptance and safe tuning remain future work.
