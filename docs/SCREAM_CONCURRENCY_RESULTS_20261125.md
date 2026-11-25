# Scream Flash Next concurrency: 25 November 2026 (UTC)

Hardware window: i7-5960X (8 cores / 16 threads), 96GB installed RAM
(94.1GiB reported by Linux), two Tesla P100 PCIe 16GB cards, NVIDIA 580.178.04.
Both cards retained their configured 150W limits. No production profile, engine
service, dashboard, driver, fan setting or boot policy was changed.

This measures the installed Flash Next bundle and executable, with MTP K=4 and
lookup 15 retained. Synthetic Python repository prompts have a distinct nonce per
session and a marker check; this is a capacity/scheduling benchmark, not an
assessment of coding correctness or agent tool use. K means 1,024 tokens here.
Requests use temperature zero and thinking disabled. Retaining the configured
speculation flags is not a measurement of draft acceptance in each scheduling mode.
The model shard is `Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf`.
The 512K case retains the existing profile's 2× RoPE scaling; passing a length
probe does not validate retrieval or coding quality across that extended window.

## Capacity method

Short 8K screening requests established that one through four slots can serve
requests, including eight queued clients at four slots. They do not prove that
each slot can retain the entire configured context.

Near-limit probes therefore use separate, calibrated input histories and monitor
actual native slot states. A multi-slot residency confirmation requires all
requested slots observed decoding with the requested input histories, and no
context parking/pausing in the native log. Merely accepting HTTP requests or
overlapping their streams is insufficient. The pinned wrapper can briefly expose
stale prompt-read progress on a newly assigned slot, so reading-state counts are
excluded from full-context confirmation. Earlier results were reanalyzed using
this stricter criterion without altering their original evidence.

The original low-RAM recipe had a 13GiB dynamic expert limit. It could complete
two large requests by parking a conversation in host memory. Removing that cap
keeps all experts backed by host copies and lets the engine lend more GPU expert
storage to KV. Raising `FNX_EXPERT_RAM_GIB` to 34 further exchanges GPU expert
storage for KV capacity. These are temporary test recipes; the model and MTP
weights, kernels and routing policy remain unchanged.
Retained settings include `FNX_ROUTE_PRIOR=0`, `FNX_PF_STAGE_ROWS=256`,
`FNX_PF_STAGE_ALL=1`, `FNX_PF_STAGE_MIB=1024`, and `FNX_PF_TAIL_MIB=3000`.
This window compares concurrency and capacity; it does not exhaustively optimize
every expert-placement or prefill parameter for each input length.

The installed Scream revision is `d30304071205c5b85a5810c01e48a49e53f3f29b`;
the tested native executable SHA-256 is
`b240363b6ea20cefb77654bf920d16ac442035fbcf47e69d1ee079af74beed12`.

The engine's `--max-context` also affects shared KV pool allocation. For example,
a 128K engine window underallocated the pool for four 128K histories in the tested
recipe. A 256K engine window allocates a larger pool, while actual per-client
input/output budgets can still be kept within 128K. This must not be described as
four resident 256K contexts.

## Completed results

The following rows use the flexible 34GiB expert-placement recipe, dynamic expert
cap unset, `FNX_PLE_RAM=0`, and a 2,000MiB conversation-cache budget. Inputs leave
room for the requested responses; these are cold capacity probes, not performance
rankings. The four-way 128K-input repeat uses longer responses to keep all four
slots decoding together long enough for the strict observation check.

| Slots | Engine window | Actual input per client | Output per client | Longest request | Peak GPU temperature | Minimum available host RAM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 512K | 522,220 tokens | 256 | 615.8s | 53°C | 27.9GiB |
| 2 | 256K | 260,067 tokens | 256 | 580.1s | 53°C | 28.2GiB |
| 3 | 128K | 129,005–129,007 tokens | 256 | 422.8s | 52°C | 27.9GiB |
| 4 | 64K | 63,477–63,485 tokens | 256 | 278.8s | 51°C | 28.1GiB |
| 4 | 256K | 129,003–129,009 tokens | 1,024 | 610.3s | 52°C | 28.5GiB |

Every row completed its session-marker checks and had zero swap growth. The
multi-slot rows also passed the completed-prefill residency criterion with no
parking. The single 512K case spent 611.6s in cold prefill and then decoded at
about 60.6 tokens/s as measured from the response stream.

The final row confirms four roughly 128K input histories with the larger shared
pool, not four full 256K contexts. A serving configuration based on that row
needs an explicit per-client 128K total budget. An 8,192-token response allowance
requires correspondingly less input than the 1,024-token probe used here. The
current production settings and advertised output limits were not changed.

## Matched-load method

The speed comparison uses the same four clients, 8,192-token initial input
targets, 1,024-token response budgets and 64K engine window at each slot count.
The flexible 34GiB recipe is retained, with a 12,288MiB conversation-cache budget.
Three trials each have a new-conversation turn and a follow-up preserving that
client's exact history. Cold means a new conversation with the model already
loaded and warmed; model startup is excluded. Thinking remains disabled.

Aggregate throughput is total output tokens divided by the synchronized wave's
makespan, including prefill and queueing. Full-response completion latency and
first-token latency are different measurements. Twelve requests per turn give
only a coarse p95, not a production service-level estimate. Follow-up cache use is
checked against native `cache_n`, rather than inferred from a shorter response.

All 96 responses produced exactly 1,024 tokens and passed their marker checks.
Every cold request reported `cache_n=0`; every follow-up had a native cache hit
(8,150–9,215 tokens). All four cases passed, reached at most 49°C, retained at
least 27.8GiB available RAM and had zero swap growth.

| Turn | Slots | Aggregate output tokens/s | Median first token | p95 first token | Median completion | p95 completion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold | 1 | 42.5 | 44.6s | 81.1s | 60.0s | 96.9s |
| Cold | 2 | 45.6 | 35.1s | 64.3s | 71.9s | 91.6s |
| Cold | 3 | 46.9 | 21.9s | 71.6s | 73.2s | 88.5s |
| Cold | 4 | 50.1 | 21.9s | 35.6s | 80.6s | 83.2s |
| Follow-up | 1 | 72.8 | 22.0s | 43.5s | 35.3s | 56.3s |
| Follow-up | 2 | 66.7 | 12.0s | 46.5s | 48.0s | 62.1s |
| Follow-up | 3 | 66.4 | 3.4s | 50.3s | 48.6s | 67.4s |
| Follow-up | 4 | 64.3 | 3.2s | 7.4s | 62.4s | 65.1s |

For four simultaneous clients in this fixture, one slot wins cached aggregate
throughput and median full-response latency. Concurrent HTTP clients can still
queue through that slot and reuse separately cached histories. Four slots win
cold aggregate throughput and first-token tail latency; median response
completion takes longer. Four slots
improved cold aggregate throughput about 18% over one slot, but reduced cached
aggregate throughput about 12% and increased cached median completion about 77%.
Neither configuration dominates every metric.

For maximum completion speed in an established coding conversation, the one-slot
recipe is the first candidate. For several clients that prioritize starting their
streams promptly, four slots are the alternative candidate. These conclusions
apply to the tested 8K-input/1K-output workload and fixed memory recipe; they are
not a claim that all context lengths, tool-call shapes or arrival patterns have
the same winner. No candidate is automatically installed or selected.

A separate two-client, 256K-window acceptance probe uses one trial at each of one
and two slots, with a 130-second monitored idle gap before follow-ups. The gap
is not included in request latency. These focused probes are excluded from
three-trial performance rankings.

The one-slot case passed with two 258,807-token cold inputs and full 1,024-token
responses. After the pause, each follow-up reused 259,830 cached tokens and
prefilled only 44 new tokens. Native cache hits confirm reuse across both client
histories; no idle-parking event was logged for the one-slot mode.

| Slots | Cold first-token range | Follow-up first-token range | Cold completion range | Follow-up completion range | Cached tokens per follow-up |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 283.5–584.1s | 2.6–17.6s | 299.5–600.1s | 15.3–30.1s | 259,830 |
| 2 | 283.5–571.8s | 3.1–4.9s | 597.5–602.7s | 36.8–40.2s | 258,796–259,822 |

The one-slot case reached 52°C, retained at least 26.1GiB available RAM and had
zero swap growth. The two-slot case also passed every marker and full output
budget, reached 52°C, retained at least 25.8GiB available RAM and had zero swap
growth. It logged two expected idle-parking events. Its follow-ups reused
258,796 and 259,822 tokens, prefilling 1,070 and 40 new tokens respectively.
The deliberately parked case's conservative multi-slot residency flag is false;
the earlier no-parking two-slot probe establishes resident capacity separately.

These focused checks confirm that the tested cache budget can preserve two long
client histories across the pause. They also show the practical tradeoff: one
queued slot completed the cached pair in 30.1s; two slots started both streams
within 4.9s and completed the pair in 40.2s. They are acceptance observations from
one trial per setting, rather than a statistically established long-context ranking.

## Final state

After all tests, both GPUs had no compute process and 0MiB used VRAM. Their
requested and enforced limits were 150W, and the driver remained 580.178.04.
The two original Scream profile files matched their pre-test hashes, as did the
native executable. No experiment user service remained active and the experiment
lock was free. The dashboard health endpoint returned `{"status":"ok"}` and its
UI returned HTTP 200.

Scream remains stopped, matching its state at the beginning of the window.
No tested recipe was installed as a production profile, selected in the dashboard,
or applied to Oh My Pi. Applying a candidate and its client budgets is a separate
reviewed configuration change.

## Containment and limitations

Tests run as the existing non-root engine owner, in uniquely named transient user
services with cgroup memory limits and no swap allocation. A separate once-per-second
guard checks GPU temperatures, the 150W limit setting, host RAM and swap. The
cutoff is 75°C and the RAM reserve is 8GiB. Each candidate has a private credential
and an authenticated loopback-only endpoint; no additional LAN API is exposed.

An early high-RAM recipe's six-client screen reached the RAM reserve and stopped.
Other rejected cases include insufficient startup VRAM and an advertised shared
KV pool smaller than the requested histories. These are distinct from thermal
failures. Cases skipped after such a boundary are untested, not proven impossible
under every possible recipe.

Sampled power draw and configured power limits are separate measurements. The
150W limit setting stayed in place; individual instantaneous samples can be above
150W. This report does not treat that setting as an observed per-sample ceiling.
For the two-slot 256K capacity probe, samples at at least 80% GPU utilization
averaged 135.7W and 137.1W per card. Individual sampled peaks were 189.2W and
194.4W; every sampled limit setting was 150W. These are GPU telemetry readings,
not whole-system energy measurements.

Private profiles, API keys and raw server logs remain on the server. Reviewed
summaries and CSVs are retained as local operator artifacts. See
[the runner procedure](SCREAM_CONCURRENCY.md) for reproduction and interpretation.
