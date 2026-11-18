# Dependency and licensing review

The measured-history feature adds no runtime dependencies and bundles no private measurement archives, model weights or native engines. Existing dependencies retain their licenses. A newly reported unpatched picomatch build-tool vulnerability is tracked in MEASURED_HISTORY.md; this is a security status change rather than a license change.

Reviewed installed package metadata and frontend package-lock.json on 2026-11-15. Project code is Apache-2.0; upstream license notices remain applicable. This is an inventory, not a claim that external engine/model licenses have been cleared.

| Component | Verified version | Declared license |
|---|---|---|
| FastAPI | 0.163.0 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| Pydantic / pydantic-core | 2.14.3 / 2.50.4 | MIT |
| Starlette | 1.10.0 | BSD-3-Clause |
| React / React DOM | 19.3.1 | MIT |
| Vite | 7.3.8 | MIT |
| TypeScript | 5.9.3 | Apache-2.0 |
| Playwright | 1.65.1 | Apache-2.0 |
| pytest | 9.2.0 | MIT |
| HTTPX | 0.28.1 | BSD-3-Clause |

Python runtime transitive metadata: annotated-doc, annotated-types, anyio, h11, typing-inspection (MIT); click and idna (BSD-3-Clause); opentelemetry-api (Apache-2.0); typing-extensions (PSF-2.0). Exact Linux runtime versions are recorded in backend/requirements.lock. Frontend transitives declare MIT, ISC or BSD-3-Clause in the inspected installed tree; optional platform packages and versions are recorded in package-lock.json. Recheck upstream notices when updating dependencies or publishing binaries.

Development-only HTTPX brings certifi (MPL-2.0 certificate bundle); preserve its upstream notices and source obligations if redistributing that component. Other inspected test dependencies use MIT/BSD or a dual Apache/BSD license. They do not change the license of this project's original code.

Docker base images contain separately licensed Debian/Linux and Python/Node components, including copyleft OS packages. A published container needs its own complete OS/package notice and source-compliance review; do not relabel the whole image Apache-2.0. Base image tags are mutable; pin reviewed image digests before release. The repository does not publish container images in CI.

Kmic, PXA and qr0n are local engine labels; exact upstream repositories/revisions/licenses remain **unverified**. Their names do not establish a license grant. No engine code, CUDA toolkit, NVIDIA driver libraries or model weights are bundled. NVIDIA runtime libraries may be injected by the administrator's toolkit and retain NVIDIA's separate terms. Model weights, quantizations and datasets also retain their own use/distribution restrictions. Verify provenance before downloads, integration, linking or redistribution.

The full [Apache-2.0 license](https://www.apache.org/licenses/LICENSE-2.0) is in LICENSE. Preserve dependency copyright and license notices in redistributed assets. No engine license constraint has been waived by adopting Apache-2.0 for the dashboard/API.

## Observed native-engine licensing (planning only)

Read-only host inspection identified Kmic-68/llama.cpp at bbad6fcfb and qr0n/p100-llama-cpp at dc5c644 with MIT root licenses. PXA source at 09c9dc3 has an MIT root license; the installed v4.0.4 bundle claims a different source revision (76a9eb9), not present in the local checkout. Its libggml-pxqn.so is explicitly excluded from the MIT grant and has separate restrictions on modification, paid redistribution and license/model-check circumvention. The lab neither bundles nor links these components. Keep the PXQN binary outside the Apache-2.0 distribution and any open-source engine fork unless separate rights are obtained. Bundled NVIDIA libraries/model weights require their own checks. This observation is not a clearance of complete bundle redistribution. Phase 2 fixtures introduce no new third-party runtime dependencies.

The optional native runner adds no third-party runtime dependencies and bundles no engines, weights or GPU libraries. PXA remains excluded from execution support pending protocol and license review.
