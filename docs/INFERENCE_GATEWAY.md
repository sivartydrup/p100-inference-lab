# P100 inference gateway

Use the `p100` provider in Oh My Pi. The gateway is available on the lab LAN
at `http://192.168.0.188:8090/v1`. It requires its own inference key, stored
on the server at `~/.config/p100-gateway/gateway-key`.

```yaml
providers:
  p100:
    baseUrl: http://192.168.0.188:8090/v1
    api: openai-completions
    apiKey: P100_INFERENCE_KEY
    discovery:
      type: openai-models-list
    compat:
      streamFirstEventTimeoutMs: 1800000
```

Set the `P100_INFERENCE_KEY` environment variable before starting the client.
The same URL and key work for Kmic, Kmic MoE, Strata, Scream P100, Inkling 2 Mini, PXA, and qr0n. The gateway lists
configured aliases and context limits from `GET /v1/models`. Each entry's
`available` field indicates whether that alias is currently loaded.
`POST /v1/chat/completions` routes by the request's model ID and passes through
chat completions, tool calls, and streaming events. A configured alias that
is offline returns HTTP 503. Switch dashboard-managed engines in **Engine
services** and wait for **Ready**. The two P100s serve one
engine at a time.
The longer first-event timeout accommodates fresh 256K prompts.

| Engine | Serving profile | Gateway model ID |
| --- | --- | --- |
| Strata | Flash-Next UD-IQ3_XXS, 256K, MTP | `qwen3.8-flash-next-unsloth-ud-iq3_xxs` |
| Scream P100 | Flash-Next GSQ-RCO IQ3_S, 256K, exact routing, MTP and prompt lookup | `scream-flash-next-iq3-s` |
| Scream P100 512K | Same Flash-Next IQ3_S weights, 524,288-token YaRN profile | `scream-flash-next-iq3-s-512k` |
| Inkling 2 Mini | NVFP4-Q8_0, 256K FP16 KV, DSpark and inline images | `inkling2-mini` |
| Kmic | Existing approved 27B and Flash-Next profiles | `qwen3.8-27b-p100`, `flash-next-fast`, `flash-next-reasoning`, `flash-next-full-context` |
| Kmic MoE | Flash-Next UD-IQ3_XXS, 256K | `flash-next-moe` |
| PXA | Qwen3.8-27B UD-Q6_K, 32K | `pxa-qwen3.8-27b` |
| qr0n | Qwen3.8-27B UD-Q6_K, 32K | `qr0n-qwen3.8-27b` |

Install the backend with its `gateway` extra and use the
[example user unit](../deploy/p100-inference-gateway.service.example). The
gateway's private JSON configuration contains `key_file`, an `upstreams`
mapping keyed by engine with `url` and `key_file`, and a `models` mapping
keyed by alias with `engine`, `name`, `context`, `max_output`, and
`reasoning`, and optional `vision`. Configure fixed local upstream addresses
and keep the key files private. See [Scream P100](SCREAM_P100.md) for the
fixed profile metadata used by Engine services and Model library.

The LAN listener uses HTTP, so the bearer key and request content travel over
the lab's trusted network. For a loopback-only deployment, bind the gateway
to `127.0.0.1` and reach port 8090 through an SSH forward or TLS proxy.

## Output limits

`models.<alias>.max_output` is advertised as `limits.max_output_tokens` by
`GET /v1/models`. The gateway forwards a client's output request unchanged;
this metadata is not an enforced request cap. On this host the
`scream-flash-next-iq3-s-512k` route was updated to 8192; other routes retain
their existing values. Keep the separate agent serving-profile metadata aligned
for client exports. Changing private routes requires restarting only the gateway,
which can interrupt gateway requests; it does not restart the native engine.

A [credential-free routes example](../deploy/inference-gateway.routes.example.json) records the admitted aliases and output limits. Replace `YOUR_USER`, review paths and ports, and create distinct private key files before use. It contains paths to credentials, never credential values.
