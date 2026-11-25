# Deployed engine reconciliation — 25 November 2026

This change captures the previously uncommitted engine/dashboard integration on
main after fan-controls PR #15. It does not install services, change running
engines, migrate databases or replace a deployment.

## Sources reconciled

- The `codex/strata-engine` working tree supplied the multi-engine API/agent,
  fixed-profile model library, authenticated inference gateway, service examples
  and Strata/Scream/Inkling guides.
- The `codex/flash-next-full-context` working tree was compared file by file.
  Its full-context launch/selection test and clearer parameter-validation message
  are retained; its other functional changes are already included or superseded
  by the multi-engine integration. The catalog entries match semantically.
- Both original working trees are preserved for comparison. No private outputs,
  model files, credentials, databases or operator deployment packages are committed.
- Fan filesystem hardening remains in separate PR #16; review and merge that
  alongside this change to match the deployed broker policy.

## Deployment correspondence and limits

The reconciled frontend builds to `index-Cb9uVW6j.js` and
`index-wS8JVBKX.css`, matching the fan overlay deployed on this host. The public
source includes the merged fan feature as well as newer engine integrations.
The routes example records the live 8192-token discovery value for
`scream-flash-next-iq3-s-512k`. Other route limits are retained.

Private host state is deliberately not made reproducible by copying secrets:
root-owned agent configs, credential files, native engine installations, model
shards, generated Scream profiles, database/history and power/fan boot policy
remain administrator-owned deployment state. Follow the linked guides and
perform host acceptance before a new installation. Agent serving-profile output
metadata is separate from gateway discovery and must be kept aligned by an
administrator if exports should advertise the same value.

The 512K profile extends context with YaRN; short recall tests do not establish
full-window quality. Multi-slot concurrency and four-GPU configurations are not
validated by this reconciliation. External engine/model licensing is unchanged.
