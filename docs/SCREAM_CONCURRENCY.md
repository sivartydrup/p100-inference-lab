# Scream Flash Next concurrency experiments

**Prepared only. No live inference run or production configuration change is
part of this feature.** Use a dedicated maintenance window. The runner refuses
active managed engines and any existing GPU compute process; it never stops
them for you. Pause all coding clients and stop the selected engine through
the existing dashboard before explicitly executing a test.

The [runner](../tools/scream_concurrency.py) uses Python's standard library.
Running it without `--execute` only prints an offline plan, including on Windows.
It does not inspect hardware, open a socket, start a subprocess or read secrets.

## Questions and matrix

We need to distinguish accepting multiple HTTP clients, actual overlapping
generation, allocating multiple slots, and fitting long prompts in every slot.
A successful short request is not evidence of full-context capacity.

| Dimension | Prepared values |
| --- | --- |
| Requested generation slots | 1, 2, 3, 4 |
| Requested context per request | 32,768; 65,536; 131,072; 262,144; 524,288 |
| Simultaneous clients | 1, slot count, twice the slot count (deduplicated) |
| Screening | About 8K input tokens, up to 256 output, one trial, one turn |
| Capacity probe | Near-limit input in every slot simultaneously, up to 256 output, one wave, one turn |
| Capacity | Near context limit with output/follow-up reserve, up to 1,024 output, three trials, two turns |
| Longer output check | Optional `--output-tokens 8192`; input reserve adjusts accordingly |

The default plan contains 20 independently loaded engine configurations. This
is an exploration range, not a claim that all combinations fit or are supported.
The actual `/props` slot count and reported context must equal the requested
values; fallback is a failed configuration. Capacity trials fill independent
conversations concurrently to test whether that reported context is really
usable per request. The 512K cases retain the current YaRN x2 extension; shorter
cases remove it. Context-extension quality is a separate question.

**HTTP success alone is not a resident-capacity pass.** The engine can queue
requests or park conversations in host memory when its K/V pool is short.
`context_evidence` separately records active slots, near-full active slots and
parking events. Multi-slot residency is confirmed only when all requested slots
are observed near-full together and no parking is logged. Missing samples or
parking leave residency unconfirmed, even when every request completes.
The engine context limit also sizes its shared K/V allocation; it is not a
guarantee that every slot can use that entire window together. Engine window and
tested initial input target are recorded separately. For example, a server window
of 262144 with `--prompt-tokens 129024` probes roughly 128K per client while
allocating the larger shared pool. Confirmation then covers those input targets,
not four full 256K windows. Keep client budgets explicit when using such a recipe.
For probes, the runner also records the pinned engine's advertised aggregate
free/lendable K/V positions. If that necessary bound is below the requested
simultaneous input/output occupancy, it skips the measured wave and records the
allocation failure. This is a configuration-bound rejection, not an observed
thermal failure or a claim about every possible recipe. Unknown log formats do
not produce a bound; actual observations and the guards still apply.

Keep one baseline memory recipe fixed across a matrix. The runner copies the
selected private base profile, preserving MTP, lookup, conversation cache,
expert-placement and memory settings. Comparing the existing 256K low-RAM recipe
directly against the existing 512K high-RAM recipe would mix several changes.
Choose one base profile per experiment, then repeat with the other if useful.
Record hardware changes in separate output directories; 96GB and 128GB results
are separate experiments. This version deliberately refuses anything other
than two Tesla P100 PCIe 16GB cards; four-card testing needs a reviewed update.

## Window procedure — do not execute while serving agents

1. Review and merge the preparation PR. Use a clean checkout of that revision on
   Ubuntu; do not overwrite a dirty/deployed checkout or install anything into
   the running engine. No download or upstream rebuild is needed.
2. When the window is available, pause/disconnect all clients and stop the
   selected engine using the existing dashboard. Wait for the GPUs to be idle.
   The runner will also check every managed engine and NVIDIA's compute-process
   list. It refuses a busy port 18087. No root privileges are needed.
3. Begin with a small screen. The following are **future-window commands**, not
   instructions to interrupt the current service:

   ```bash
   # Offline: can be run now, on either OS.
   python3 tools/scream_concurrency.py --slots 1 2 --contexts 32768

   # Ubuntu, only after the operator has made the window available.
   python3 tools/scream_concurrency.py --execute --window-confirmed \
       --base-profile "$HOME/inference/scream-flash-next-512k/profile.json" \
       --slots 1 2 --contexts 32768 --tier screen \
       --output "$HOME/p100-concurrency-screen-01"
   ```

4. Expand the screen with `--slots 1 2 3 4` and
   `--contexts 32768 65536 131072 262144 524288`. A full matrix requires 20 model
   loads and can take hours. Start with 32K/64K and grow the selection if the
   available window is short. Never treat an interrupted/unattempted case as
   an unsupported capacity result.
5. For screen survivors, first use `--tier probe` for one near-limit simultaneous
   wave at slot-matched concurrency. This identifies capacity failures before
   spending hours on repeated long prompts. A probe is not eligible for automatic
   candidate comparisons. Then run repeated capacity tests on the survivors:

   ```bash
   python3 tools/scream_concurrency.py --execute --window-confirmed \
       --base-profile "$HOME/inference/scream-flash-next-512k/profile.json" \
       --slots 1 2 --contexts 65536 131072 262144 --tier capacity \
       --output "$HOME/p100-concurrency-capacity-01"
   ```

6. Repeat finalists with `--output-tokens 8192` to represent the current client
   allowance. Compare repeated results and then perform representative coding,
   tool-call and sustained-load acceptance before selecting production settings.
   The synthetic workload is not an agentic coding correctness benchmark.
   For a matched-load comparison, use `--clients 4 --prompt-tokens 8192` with
   `--tier capacity`: every slot configuration then serves the same four clients
   with about 8K initial input, three trials and two turns. This is a representative
   performance workload, not a near-limit capacity demonstration. The override is
   recorded explicitly; the runner refuses budgets that cannot fit with reserves.
7. After completion, the candidate is stopped; the runner does **not** start or
   restore any production engine. Choose the desired existing profile and Start
   it explicitly in the dashboard when ready. Production boot settings are untouched.

## Workload and measurement

Each agent gets a distinct early session identifier and a generated synthetic
Python repository with numbered functions. There is no private source upload.
Prompt size is calibrated using the candidate's count-only endpoint; actual
chat prompt usage must agree within 128 tokens and stay inside the context
budget. The token-count endpoint uses Anthropic conversion, so disagreement is
a failed test, not a trusted approximation. Targets are within 2%, leaving
reserve for template overhead, outputs and follow-up turns.

All clients in a wave are released by a barrier. Requests disable thinking,
use temperature zero and retain the profile's speculation/lookup defaults.
Unique initial prefixes reduce cross-agent prompt-cache reuse. Follow-up turns
reuse only their own exact assistant history. The expected session marker is
checked on every turn; this is a small isolation/correctness signal, not proof
of coding quality. Model-global experts/page caches are warmed naturally; these
are **new-conversation** tests, not cold-disk benchmarks. Page caches are never
dropped. One short, separately recorded warmup precedes measured waves.

Results include:

- Per request: actual input/output tokens, time to the first content **or
  reasoning** token (ignoring role-only frames), first content latency, total
  latency, finish reason, streamed decode-rate estimate and output digest.
  Optional engine timing fields retain self-reported cached/read token counts and
  native prefill/decode time; client wall-clock measurements remain separate.
- Per wave: aggregate output tokens divided by the synchronized wave's makespan,
  including prefill and queueing; first-token and full-response latency median/p95;
  observed overlap of generated
  response streams. Streaming overlap is a scheduling signal, not kernel profiling.
- Once-per-second telemetry: per-GPU UUID, temperature, power, VRAM and utilization;
  host available/total RAM, swap and load. The summary retains resource peaks and
  the minimum RAM reserve. It does not label sampled power as energy consumption.
- Independent read-only slot sampling twice per second records actual reading and
  decoding states. It has a short HTTP timeout and cannot block the hardware guard.
  The engine log is checked for K/V parking/pausing. These measurements distinguish
  active GPU-resident contexts from server-reported slot counts and time-slicing.
  Full-context confirmation counts decoding slots only: the pinned wrapper can
  briefly attach stale prompt-read progress to a newly assigned reading slot.
- Cold/new-conversation and follow-up results remain separate. A three-trial p95
  is a coarse order statistic, not a statistically strong service-level estimate.

`summary.json` compares configurations. `candidates.json` identifies the highest
aggregate throughput and lowest p95 first-token latency among successful repeated
trials with the same context, turn, client count, initial prompt target and output
budget. It never compares unlike client loads as if they were equivalent. It excludes
screening and runs whose median output is below 90% of the requested budget.
These are candidates for further testing, not an automatic production change.
Performance comparisons can include queued requests; check `context_evidence`
before making any simultaneous-residency claim. The residency criterion is
conservative: even a temporary parking event leaves it unconfirmed.
Twice-slot-count waves show queueing behavior separately from slot-matched work.

## Containment, aborts and evidence

Each candidate uses a uniquely named **transient user service**, its own private
profile/credential/logs, and authenticated loopback port 18087. There is no new
LAN listener, Docker change, persistent unit or daemon reload. The runner never
changes a fan mode, GPU power limit, persistence setting, driver or boot policy.
A private runtime lock prevents two copies of this runner from executing at once.
Keep other agents stopped throughout the window; this lock is not a lock shared
with arbitrary external launchers.

The transient service has `MemoryMax` equal to initially available host RAM minus
the reserve, `MemorySwapMax=0`, a four-hour ceiling and cgroup-wide termination.
The runner verifies the candidate's effective cgroup-v2 memory/swap files early
in startup and again before requests. Unsupported delegation fails closed.
It does not weaken these checks to make a configuration pass.

The default host RAM reserve is **8GiB**. A once-per-second guard aborts at 75°C,
if either enforced power limit differs from 150W, if RAM falls below the reserve,
if swap grows over 256MiB, or if telemetry becomes unavailable. It stops only the
candidate's transient cgroup. Ctrl+C/SIGTERM also clean up that candidate. Host
resource guards cannot guarantee prevention of every abrupt hardware/OS failure.
The static cgroup limit limits this candidate's host-memory consumption in addition
to sampled protection. GPU OOMs remain possible and are recorded as failures.

On a normal candidate failure/fallback, larger contexts at that slot count are
skipped, then the next slot-count ladder can be tried after idle checks. A resource
guard trip or operator interruption ends the entire run for review. Capacity
limits are only demonstrated by attempted, completed workloads; skipped cases
remain unknown. If process cleanup leaves any GPU compute process behind, the
next preflight refuses to start another candidate.

The output directory must be new and its parent must exist. All artifacts are
private: generated profiles and raw server logs can contain credentials. Do not
commit or publish them. Preserve failed/partial evidence, and share only reviewed
redacted summaries. The tool records the base-profile hash, executable hash and
upstream revision without downloading weights or hashing the full model bundle.
Model identity/provenance still depends on the previously admitted base profile;
this runner is not a replacement for artifact admission checks.

The preparation tests use synthetic HTTP/telemetry fixtures only. They do not
constitute hardware acceptance of any slot/context combination.

References: [pinned Scream guide](https://github.com/Megla123/Scream-P100/blob/d30304071205c5b85a5810c01e48a49e53f3f29b/docs/GUIDE.md),
installed Strata v0.2.9 `serve/server.py` (`/props`, count-only endpoint and stream
usage), and [current host profiles](SCREAM_P100.md).
