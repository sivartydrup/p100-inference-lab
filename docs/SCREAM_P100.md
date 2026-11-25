# Scream P100 Flash-Next

Use **Scream P100** in Engine services. Stop the running engine and wait
for both cards to become idle, then start Scream and wait for **Ready**.
Select **Flash Next IQ3_S 256K (Scream P100)** in Model library to see the
profile limits and download an Oh My Pi preset for the shared gateway.
The model ID is `scream-flash-next-iq3-s`.

The service uses the upstream IQ3_S 64 GB recipe at 262,144 tokens. It
uses exact expert routing, one slot, MTP and prompt lookup. The lookup
table is memory-mapped from NVMe and read through the Linux page cache.
The optional image projector runs on the CPU. Keep room in the context
window for generated tokens.

## Host installation

The profile needs Linux, two PCIe Tesla P100s with 16 GiB VRAM and 16 GiB
BAR1 each, CUDA 12, a compatible G++ and NVMe storage. The engine revision
is `d30304071205c5b85a5810c01e48a49e53f3f29b`. Its setup script pins the
Strata API server at v0.2.9 and checks the ISTA-DASLab shards and vision
projector with SHA-256.

```bash
cd ~/inference
git clone https://github.com/Megla123/Scream-P100.git scream-p100
cd scream-p100
git checkout --detach d30304071205c5b85a5810c01e48a49e53f3f29b
CUDA=/usr/local/cuda-12.8 bash build.sh
umask 077
CUDA=/usr/local/cuda-12.8 bash setup.sh "$HOME/inference/scream-flash-next" \
    --models "$HOME/inference/models/scream-flash-next-iq3_s" --ram 64 --gpu 0,1
```

In the generated private `profile.json`, set `model_name` to
`scream-flash-next-iq3-s`, `port` to `8085` and `api_key` to a distinct
random key. Store the same key in a private `inference-key` file next to
the profile for the gateway and agent. Install the [user unit](../deploy/p100-lab-scream.service.example)
and [shared GPU guard](../deploy/gpu-engine-idle.sh), then reload the user
manager. Review the [upstream guide](https://github.com/Megla123/Scream-P100/blob/d30304071205c5b85a5810c01e48a49e53f3f29b/docs/GUIDE.md#64-gb-of-ram)
for the settings and expected startup memory.

Add a `scream` engine to the sealed [host agent](ENGINE_AGENT.md)
configuration with the unit's absolute path, file digest, loopback
endpoint, key path and label. For fixed services, an optional
`serving_profile` object publishes the configured model in the dashboard.
Use the same alias, context and capabilities in the
[gateway route](INFERENCE_GATEWAY.md).

```json
{
  "alias": "scream-flash-next-iq3-s",
  "quantization": "IQ3_S",
  "context": 262144,
  "max_output": 4096,
  "reasoning": true,
  "vision": true
}
```

The guide's benchmarks use a freshly loaded engine. Record cache state,
GPU power limit, the prompt size and the time to the first **content or
reasoning token** when comparing inference. Strata can send an initial
stream event containing only the assistant role.

## Flash-Next 512K

**Scream P100 512K** uses the same IQ3_S weights, tokenizer, drafter,
and vision projector. Its separate profile extends the trained 262,144
positions to 524,288 with YaRN x2. Leave space for the response inside
the total context window. Stop the running engine and wait until both
cards are idle before starting this profile. In Oh My Pi, select
`p100/scream-flash-next-iq3-s-512k`.

Generate the second profile with the pinned upstream
`profile/fill_profile.py --model iq3_s --ram 96 --route-prior 0 --gpu 0,1`
and pass the existing model, MTP, tokenizer, and vision paths. Create the
separate logs directory before startup and save the profile to
`~/inference/scream-flash-next-512k/profile.json`. Give it the 512K model
ID, loopback port 8087, and its own private API key. Install the
[512K unit](../deploy/p100-lab-scream-512k.service.example), then admit the
`scream-512k` endpoint with context 524288 in the agent and gateway.
The [upstream profile guide](https://github.com/Megla123/Scream-P100/blob/main/docs/GUIDE.md#install)
describes the KV cache and RAM requirements.

## Inkling 2 Mini

The Scream repository's [Inkling runner](https://github.com/Megla123/Scream-P100/blob/ad61bf6ba6e6ff07696f3f62a99903a9e59a3769/docs/INKLING2.md)
uses its own engine. The lab tested the pinned build at 262,144 tokens
with FP16 KV, DSpark and the CPU vision projector. Select **Inkling 2 Mini**
in Engine services to load it, then use `p100/inkling2-mini` in Oh My Pi.
Its fixed profile is also visible in Model library.

Install the [Inkling unit](../deploy/p100-lab-inkling.service.example) and
[authenticated launcher](../deploy/inkling-server.py) after building and
preparing the pinned upstream artifacts. The launcher takes a private key
from `~/.config/p100-gateway/inkling-key` and listens on `127.0.0.1:8086`.
Add the `inkling` unit and profile to the host agent and gateway with
the same procedure as Scream. The launcher accepts inline image data URLs.

Host acceptance reports are private operator artifacts, excluded from this repository.
The historical checks exercised streaming, tool calls and image input at 150 W per card.
Recall prompts covered 41,840 tokens for Flash-Next and 36,142 for Inkling;
these checks do not establish full-window recall or multi-slot capacity.

The current 512K gateway route advertises 8,192 output tokens; the 256K route
advertises 4,096. Set `max_output` in the private gateway routes configuration.
Agent `serving_profile.max_output` is separate metadata used in dashboard exports;
keep it aligned when changing an admitted host configuration. No settings are
changed automatically by checking out this repository.
