"""Strict two-row llama-bench JSON microbenchmark parser (not TTFT/chat)."""
import hashlib
import json
import math
import statistics

from .native_config import NativeError, no_duplicate_keys


def positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise NativeError("invalid_benchmark_output")
    return value


def parse_output(output, preset):
    try:
        rows = json.loads(output.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        if not isinstance(rows, list) or len(rows) != 2:
            raise ValueError("Expected prefill/decode rows")
        expected = {(preset.prompt_tokens, 0): "prefill", (0, preset.output_tokens): "decode"}
        values = {}
        raw = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Expected object")
            prompt, generated = row["n_prompt"], row["n_gen"]
            if type(prompt) is not int or type(generated) is not int:
                raise ValueError("Invalid token counts")
            phase = expected.get((prompt, generated))
            if phase is None or phase in values:
                raise ValueError("Unexpected workload")
            for key, value in (("n_threads", preset.threads), ("n_batch", preset.batch),
                               ("n_ubatch", preset.microbatch), ("n_gpu_layers", preset.gpu_layers)):
                if type(row[key]) is not int or row[key] != value:
                    raise ValueError("Workload mismatch")
            if row["model_filename"] != preset.model.path:
                raise ValueError("Model mismatch")
            commit = row["build_commit"]
            if not isinstance(commit, str) or not 7 <= len(commit) <= 40 or not preset.engine_revision.startswith(commit):
                raise ValueError("Build mismatch")
            if not isinstance(row["backends"], str) or "CUDA" not in row["backends"].upper():
                raise ValueError("CUDA backend not reported")
            ns, rates = row["samples_ns"], row["samples_ts"]
            if not isinstance(ns, list) or len(ns) != 1 or not isinstance(rates, list) or len(rates) != 1:
                raise ValueError("Expected one engine repetition")
            duration = positive(ns[0])
            rate = (prompt + generated) * 1e9 / duration
            for reported in (row["avg_ts"], rates[0]):
                if abs(positive(reported) - rate) > max(0.01, rate * 0.005):
                    raise ValueError("Inconsistent throughput")
            if abs(positive(row["avg_ns"]) - duration) > 1:
                raise ValueError("Inconsistent duration")
            values[phase] = rate
            # Whitelist raw evidence; replace the host model path with its ID.
            raw.append({"phase": phase, "model_id": preset.model_id, "build_commit": commit,
                        "n_prompt": prompt, "n_gen": generated, "samples_ns": ns,
                        "samples_ts": rates, "avg_ns": row["avg_ns"], "avg_ts": row["avg_ts"]})
        return {"prefill_tokens_per_second": values["prefill"],
                "decode_tokens_per_second": values["decode"], "raw": raw,
                "stdout_sha256": hashlib.sha256(output).hexdigest(), "ttft_ms": None}
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
        raise NativeError("invalid_benchmark_output")


def summarize(samples):
    return {"source": "native", "sample_count": len(samples), "ttft_ms": None,
            "metrics": {k: {"median": statistics.median(s[k] for s in samples),
                            "min": min(s[k] for s in samples), "max": max(s[k] for s in samples),
                            "stddev": statistics.pstdev(s[k] for s in samples)}
                        for k in ("prefill_tokens_per_second", "decode_tokens_per_second", "process_wall_ms")},
            "notice": "CLI token microbenchmark; not chat latency, correctness or peak-memory measurement"}
