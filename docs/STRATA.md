# Strata on dual Tesla P100s

In **Engine services**, unlock the controls with the engine control token. Stop
the current GPU engine and wait for it to release both cards. Start **Strata**
and wait for **Ready**. To go back, stop Strata and then start Kmic. Strata
serves one prepared profile and launches on demand. Its card shows the
configured profile, service state, and model reported by the authenticated
inference endpoint.

The Ubuntu host uses the Strata CUDA 12 engine. Prepare the model with Strata
before installing the [example user unit](../deploy/p100-lab-strata.service.example).
Install the unit with mode `0644` and reload the user systemd manager.
The example expects a private run config at
`~/inference/strata-0.2.18/profile.json`. Use the setup-written config for GPUs
0 and 1 and configure its API key. Keep the profile and key file private.
The example uses a loopback listener on 8081. Oh My Pi can use the
[shared inference gateway](INFERENCE_GATEWAY.md) on port 8090.
The dual-P100 profile uses `--max-context 262144 --kv int8 --kv-resident 32768`,
with one slot and MTP enabled. Reserve space for the output inside the
262,144-token window.

Add an optional `strata` object to the existing root-owned agent configuration
and restart the agent. It takes `unit_file` (the absolute path to
`p100-lab-strata.service`), `unit_sha256` (the reviewed unit file digest),
`endpoint` (the local Strata base URL), `upstream_token_file` (the absolute
path to the Strata inference key), and `model_label`. The existing IPC
credential and dashboard engine control token authorize Start and Stop.

The agent checks the fixed unit identity and both GPUs before starting an
engine. Current Strata status comes from systemd and the authenticated
`/v1/models` endpoint. Start requests can be accepted before model loading
finishes. Inspect startup with
`journalctl --user -u p100-lab-strata.service -e`.
