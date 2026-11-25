# Flash Next coding profiles

The host catalog can offer **Qwen3.8 Flash Next — Fast**, **Reasoning** and **Full Context (256K)** alongside the existing 27B entry. All three use the same four UD-IQ3_XXS shards and full Q4_K_M MTP head. Each catalog ID selects a reviewed launch recipe.

## Tested defaults

| Setting | Fast | Reasoning |
| --- | --- | --- |
| Context / batch / microbatch | 32768 / 1024 / 512 | 32768 / 1024 / 512 |
| Expert cache / GPU split | 20480 MiB / 55:45 | 20480 MiB / 55:45 |
| Split / KV cache | layer / q8_0 | layer / q8_0 |
| Threads / slots | 8 / 1 | 8 / 1 |
| MTP draft / placement | 2 tokens / CUDA1 | 2 tokens / CUDA1 |
| Thinking / reasoning budget | off / 0 | on / 1024 tokens |
| Temperature / top-p | 0.7 / 0.8 | 1.0 / 0.95 |
| Top-k / min-p | 20 / 0 | 20 / 0 |
| Oh My Pi maximum output | 2048 tokens | 4096 tokens |

Flash Next parameters are fixed to the tested combination. The normal saved-preset workflow can name these settings and attach evidence. The catalog choices are immediately available after host admission; no database seeding or automatic default replacement is required. A saved preset records the reviewed recipe ID and a fingerprint covering the binary, draft head, main bundle and recipe settings, as well as engine and GPU identity. The original 27B presets remain available with their existing context/batch/microbatch values.

**Full Context (256K)** uses 262144 context, 1024 batch, 256 microbatch and a 16 GiB expert cache. It shares the Reasoning profile's request defaults. The host has passed startup and a short completion at this setting.

Choose the profile in **Model library**, stop the engine if needed, then confirm **Configure next start**. Use **Start** in Engine services when ready. The engine card shows the staged profile and the live model alias. An enabled service uses the staged selection at boot.

Engine defaults apply to clients that omit those fields. Clients can override thinking and sampling per request. **Download Oh My Pi preset** exports a credential-free models.yml fragment with explicit request settings and the selected served alias. Merge it into the existing Oh My Pi configuration rather than replacing other providers. Set the existing inference API key and verify the server address/port. The dashboard's access and engine-control tokens are separate credentials. Only the currently selected alias is served; both profiles share one engine service. Select `p100-fast/flash-next-fast` with thinking off, or `p100-reasoning/flash-next-reasoning` with thinking low. This export follows [Oh My Pi's model configuration contract](https://github.com/can1357/oh-my-pi/blob/main/docs/models.md); the smoke tests used Oh My Pi 19.0.1.

## Measurements and limits

[Compact measured evidence](FLASH_NEXT_MEASUREMENTS.json) records the dual-P100 run on 2026-11-21, at requested/enforced 150W limits and a 75°C test cutoff. With matching 20GiB expert caches, non-thinking short-screen decode medians were 24.45 tokens/s without MTP and 35.07 with MTP=2. The mixed screen improved from 46.38s to 38.40s. The five-minute complete-dialogue MTP validation passed at 33.10 tokens/s median. Its largest dialogue was below the 32K capacity; this is not a full-context or broad coding-quality validation. One small Oh My Pi task completed with all three tests passing in 41.92s (Fast) and 61.66s (Reasoning). The reasoning profile has a smoke test, not the same five-minute sustained reasoning benchmark. Temperatures peaked at 45°C and 39°C; sampled power briefly exceeded 150W while requested/enforced limits stayed at 150W.

An earlier long test appended truncated responses to the conversation and then produced degenerate replies. Replaying that history failed with and without speculation. The corrected five-minute comparison requires complete replies before appending them, and both controls passed. These results do not establish general reasoning or tool-use correctness. Standalone ngram speculative modes asserted in the installed engine and are not offered.

## Separate host admission after review

This change does **not** install a host service, replace the production dashboard, modify the native unit or start a model. Admission requires the reviewed API, agent and launcher code together. Keep the existing static unit/catalog integration until this migration is explicitly performed.

1. Back up the existing unit, sealed agent config, private selection file and dashboard database. Record the exact current hashes, boot setting and running state. Retain the original 27B catalog entry and saved profiles.
2. Merge the entries from [flash-next.profiles.json](../deploy/engine-agent/flash-next.profiles.json) into the existing config's `managed_models.models` array. Retain the existing entries and verify the engine revision. Verify every shard and the draft checksum against the pinned revision. Top-level `bytes` is the total main-model size. The bundle digest covers the ordered shard basenames, sizes and checksums. The MTP artifact has its own size and checksum.
3. Install the reviewed package, including `kmic_profiles.py` and `kmic_launcher.py`, in the existing root-owned agent runtime. Keep the configuration root-owned and non-writable by the engine account, readable by both the agent and the native service account. Preserve the existing secret files and permissions. The API still receives no model-path or state-directory mount.
4. During an approved interruption, stop Kmic and wait for inactivity/no jobs. Review the original fragment together with [the launcher excerpt](../deploy/engine-agent/kmic-launcher.fragment.example). Replace only the original ExecStart with the fixed launcher command; retain the existing required EnvironmentFile, executable path, authenticated LAN listener, address wait, cgroup/security/resource directives and boot linkage. The launcher uses exact legacy tensor/q4/MTP=4 defaults for the 27B entry and layer/q8/cache/MTP=2 defaults for Flash Next. It replaces itself with the engine without a shell. Review any local differences before migrating; this legacy recipe reproduces this server's pinned unit, not every possible Kmic installation.
5. Pin the new complete fragment digest in the sealed agent config, reload the existing user manager, and restart only the reviewed engine agent. A Flash Next catalog attached to the old direct-executable unit is refused. The launcher independently revalidates the staged selection and all artifact availability before each launch, including boot. For Flash Next it also verifies the existing engine binary's benchmarked SHA-256. An upgraded engine requires revalidation and a reviewed recipe update. Availability/stat checks do not rehash 84GB of weights on each dashboard refresh; trusted administrators/same-UID processes remain in the host trust boundary.
6. Test both aliases through a separate candidate dashboard before replacing production: dual-credential denials, explicit staging while stopped, Fast start/readiness/client defaults, stop, Reasoning start/readiness/thinking budget, stop, agent reconnect, boot-state preservation and rollback. No model download, reboot, driver/power/fan change or unrelated service restart is needed. Existing start preflight still checks both GPUs are idle, cool and have free VRAM. The launcher inherits the existing unit's resource/thermal policy; the five-minute benchmark cutoff is not a newly installed continuous temperature supervisor.
7. For rollback restore the saved fragment/config/runtime/selection, reload the user manager, reconnect the previous agent and verify the saved boot setting and selected profile. Do not automatically start 27B when restoring a previously stopped state.

The lab remains Apache-2.0. This PR adds no dependency or redistributed weights/engine binaries. Model and engine licenses remain separate; the catalog's pinned source is [Unsloth's model repository](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/f5d175de2cf7f720d45d6064019a81fcf056e986). Review the license and notices at the pinned revision before redistributing artifacts. Registering a local path does not relicense them.
