"""Reviewed launch recipes. The HTTP API never accepts flags, paths or commands."""
import json

LEGACY = "qwen27b-v1"
FAST = "flash-next-fast-v1"
REASONING = "flash-next-reasoning-v1"
FULL_CONTEXT = "flash-next-full-context-v1"
FLASH_BINARY_SHA256 = "aa6e5c2ecf13ccfb00024ed594205f0efddfe3c84dbcb05092eb1f9a9705bafc"
FLASH_SETTINGS = {"context": 32768, "batch": 1024, "ubatch": 512}
FLASH = {
    FAST: FLASH_SETTINGS,
    REASONING: FLASH_SETTINGS,
    FULL_CONTEXT: {**FLASH_SETTINGS, "context": 262144, "ubatch": 256},
}
RUNTIMES = {LEGACY, *FLASH}


def client_settings(runtime):
    thinking = runtime != FAST
    return {
        "temperature": 1.0 if thinking else 0.7,
        "top_p": 0.95 if thinking else 0.8,
        "top_k": 20, "min_p": 0.0,
        "chat_template_kwargs": {"enable_thinking": thinking},
        "reasoning_format": "deepseek",
        "reasoning_budget_tokens": 1024 if thinking else 0,
    }


def expert_cache_mib(runtime):
    return 16384 if runtime == FULL_CONTEXT else 20480


def public_recipe(runtime):
    if runtime not in FLASH:
        return None
    if runtime == FULL_CONTEXT:
        notice = "256K native context. Startup and a short completion passed on dual P100s. Long prompts await validation."
    else:
        notice = ("32K capacity; five-minute non-thinking validation and a small Oh My Pi coding smoke test. "
                  "Reasoning used a 1024-token budget. These are not broad coding-quality measurements.")
    return {
        "id": runtime, "mode": "Fast" if runtime == FAST else "Reasoning",
        "expert_cache_mib": expert_cache_mib(runtime), "tensor_split": [55, 45], "draft_tokens": 2,
        "client_settings": client_settings(runtime),
        "notice": notice,
    }


def launch_arguments(entry, settings):
    """Return fixed argv excluding the pinned executable and listener/credential flags."""
    runtime = entry.get("runtime", LEGACY)
    if runtime not in RUNTIMES:
        raise ValueError("Unreviewed Kmic runtime")
    args = ["-m", str(entry["path"]), "-a", entry["alias"], "-ngl", "99",
            "-c", str(settings["context"]), "-b", str(settings["batch"]),
            "-ub", str(settings["ubatch"]), "-np", "1", "-fa", "on"]
    if runtime == LEGACY:
        return args + ["-sm", "tensor", "-ctk", "q4_0", "-ctv", "q4_0",
                       "--spec-type", "draft-mtp", "--spec-draft-n-max", "4",
                       "--spec-draft-p-min", "0.2", "-ngld", "99", "-ubd", "64",
                       "-ctkd", "q4_0", "-ctvd", "q4_0", "--jinja", "--temp", "1.0",
                       "--top-k", "20", "--top-p", "0.95", "--min-p", "0.0",
                       "--cache-prompt", "--no-webui"]
    if settings != FLASH[runtime]:
        raise ValueError("Flash Next requires the configured recipe context, batch and microbatch")
    client = client_settings(runtime)
    return args + ["--fit", "off", "-sm", "layer", "-ctk", "q8_0", "-ctv", "q8_0",
                   "-t", "8", "-tb", "8", "--moe-cache-mib", str(expert_cache_mib(runtime)), "-cmoe",
                   "-ts", "55,45", "--load-mode", "none", "--spec-type", "draft-mtp",
                   "-md", str(entry["draft"]["path"]), "--spec-draft-n-max", "2",
                   "-ngld", "99", "-ubd", "64", "-ctkd", "q8_0", "-ctvd", "q8_0",
                   "--spec-draft-device", "CUDA1", "--jinja", "--no-webui",
                   "--temp", str(client["temperature"]), "--top-p", str(client["top_p"]),
                   "--top-k", "20", "--min-p", "0.0", "--reasoning-format", "deepseek",
                   "--reasoning-budget", str(client["reasoning_budget_tokens"]),
                   "--chat-template-kwargs", json.dumps(client["chat_template_kwargs"], separators=(",", ":"))]
