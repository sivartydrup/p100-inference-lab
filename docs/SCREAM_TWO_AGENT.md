# Two-agent queued 256K profile

**Scream P100 Two Agents (Queued 256K)** is a separate fixed service profile.
The existing single-agent 512K service, alias and boot setting remain unchanged.
Run either profile using Engine services: stop the current engine, wait for idle
GPUs, then start the desired option. They share the same existing model files
and cannot run together on these two cards.

Both coding agents use `scream-flash-next-iq3-s-two-agent` through the existing
authenticated gateway at `http://192.168.0.188:8090/v1`. Each has a 262,144-token
total context budget, including output. The advertised output allowance is 8,192
tokens. One generation slot serves their model requests in turn; agents can
still run tools concurrently. This is a tested two-agent workload, not a hard
two-connection admission limit.

## Parameters

| Setting | Value |
| --- | --- |
| Generation slots | 1 (queued) |
| Native context | 262,144; no RoPE extension |
| Conversation cache | 12,288MiB |
| Expert RAM placement | `FNX_EXPERT_RAM_GIB=34.0` |
| Dynamic expert cap | Unset |
| PLE RAM | `FNX_PLE_RAM=0` |
| Configured speculation / lookup | 4 / 15 |
| Service memory / swap ceiling | 84GiB / 0 |
| Native endpoint | Authenticated loopback `127.0.0.1:8088` |
| Automatic boot | Not enabled |

The remaining prefill/routing settings are retained from the measured recipe:
stage rows 256, stage-all 1, stage buffer 1,024MiB, tail buffer 3,000MiB and
route prior 0. No model, tokenizer, drafter or vision projector is downloaded.

The [measured results](https://github.com/sivartydrup/p100-inference-lab/blob/13e5f055013f898ef9f54aab6b638784bf704ec7/docs/SCREAM_CONCURRENCY_RESULTS_20261125.md)
cover the existing 96GB/two-P100 system at 150W per card. With two nearly full
256K histories, cached 1,024-token responses completed in 15.3s and 30.1s after
a 130-second pause, reusing 259,830 cached tokens per request. Cold first tokens
took 283.5s and 584.1s. This is one acceptance trial, not a latency guarantee or
a coding correctness assessment. Actual prompt, output and thinking settings matter.

## Oh My Pi

Select this engine in Model library and download its Oh My Pi preset. Merge the
fragment into `models.yml` and supply the existing **inference gateway key**.
Its separate provider ID preserves an existing `p100`/512K provider entry.
Both agents select the new model alias; neither should use the 512K alias while
this profile is loaded.

The downloaded preset turns thinking off for the measured fast workload.
The engine still supports reasoning and vision. Other clients must explicitly
disable thinking when they want that fast mode; this client preset does not
change unrelated gateway defaults or force every client's request parameters.

## Operator installation

Repository checkout does not install or deploy the profile. The pure builder in
`lab.scream_profiles` constructs a distinct private configuration and rejects
conflicting slot, speculation or prefill settings. The service template is
[here](../deploy/p100-lab-scream-two-agent.service.example); the sealed-agent
entry is [here](../deploy/engine-agent/scream-two-agent.example.json).

The operator deployment tool defaults to an offline plan:

```powershell
python tools/deploy_scream_two_agent.py
```

After review, a pinned source bundle and host manifest can be applied with
`--apply --window-confirmed --package DIRECTORY --manifest-sha SHA256` as root
on the existing Ubuntu installation. It refuses a changed baseline or active
inference before installation. The operator must provide the idle window; it
never stops an existing inference engine to obtain one.

The installer backs up configuration and a consistent SQLite snapshot, builds
a candidate dashboard while production stays running, installs the stopped
profile with a distinct private upstream key, and updates the sealed agent and
gateway. A disposable dashboard checks authenticated metadata, denied controls,
preserved history and fan configuration before the production switch. Rollback
restores the prior integration on failure. It does not start inference, enable
boot, restart Docker/Netdata/SSH, or write fan/driver/power settings. Brief agent,
gateway and dashboard restarts apply the new option only during the confirmed
idle window. Raw diagnostics and credentials stay in the private host backup.
