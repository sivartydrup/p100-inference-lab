# Contributing

Contributions are under Apache-2.0. Keep Phase 1 observable and read-only toward the host. Test authentication, process arguments, filesystem boundaries and persistence when changing them. Run backend tests, frontend build/browser tests and container smoke before proposing a PR. CI must pass before merge; merging and deployment remain manual review decisions.

Never add arbitrary shell endpoints, unauthenticated LAN controls, privileged containers or silent engine/driver installation. Do not commit model weights, credentials, databases or private host paths. Record engine provenance and its separate license before implementing an adapter. Keep mock samples clearly labelled and separate from measured results.
