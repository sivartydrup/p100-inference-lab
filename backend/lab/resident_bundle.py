"""Read the fixed files from a private acceptance-log directory; never execute log content."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .jobs import encode
from .measured_history import MeasuredRun, MAX_BYTES
from .native_config import NativeError, no_duplicate_keys


def prepare_bundle(directory, engine, model_id, model_license, declared_power_limit=None):
    directory = Path(directory)
    hashes = {}

    def read(name):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise NativeError("invalid_resident_evidence")
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise NativeError("invalid_resident_evidence")
        hashes[name] = hashlib.sha256(raw).hexdigest()
        return raw.decode("utf-8-sig")

    def document(name):
        return json.loads(read(name), object_pairs_hook=no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))

    def rows(name):
        reader = csv.DictReader(io.StringIO(read(name)))
        fields = reader.fieldnames or []
        if not fields or len(set(fields)) != len(fields) or any(not f for f in fields):
            raise NativeError("invalid_resident_csv")
        result = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in result):
            raise NativeError("invalid_resident_csv")
        return result

    metadata = document("metadata.json")
    summary = document("summary.json")
    started = document("measurement-start.json")
    request = document("request.json")
    warmup = document("warmup.json")
    requests = rows("requests.csv")
    telemetry = rows("telemetry.csv")
    raw_timings = [json.loads(line, object_pairs_hook=no_duplicate_keys,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
                   for line in read("timings.jsonl").splitlines()]
    if not 1 <= len(requests) <= 1000 or not 1 <= len(telemetry) <= 3600 or len(requests) != len(raw_timings):
        raise NativeError("resident_evidence_count")
    # Parse only this reviewed harness' fixed flag grammar; paths are never opened or exported.
    argv = metadata["argv"]
    pairs = {"-m", "-ngl", "-c", "-np", "-t", "-tb", "-b", "-ub", "-fa", "--fit", "--host", "--api-key-file", "--timeout"}
    switches = {"--no-webui", "--no-slots"}
    args = {}
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or Path(argv[0]).name != "llama-server":
        raise NativeError("resident_protocol_mismatch")
    i = 1
    while i < len(argv):
        key = argv[i]
        if not isinstance(key, str) or key in args:
            raise NativeError("resident_protocol_mismatch")
        if key in switches:
            args[key] = True
            i += 1
        elif key in pairs and i + 1 < len(argv) and isinstance(argv[i + 1], str):
            args[key] = argv[i + 1]
            i += 2
        else:
            raise NativeError("resident_protocol_mismatch")
    if set(args) != pairs | switches or args["-np"] != "1" or args["--fit"] != "off" or args["-tb"] != args["-t"]:
        raise NativeError("resident_protocol_mismatch")
    tokens = request["prompt"]
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= 8192 or any(type(t) is not int or not 0 <= t < 2**31 for t in tokens):
        raise NativeError("resident_prompt_mismatch")
    if set(request) != {"prompt", "n_predict", "stream", "temperature", "seed", "ignore_eos", "cache_prompt", "timings_per_token"}:
        raise NativeError("resident_protocol_mismatch")
    if request["stream"] is not True or request["timings_per_token"] is not False:
        raise NativeError("resident_protocol_mismatch")
    if metadata.get("engine", engine) != engine:
        raise NativeError("resident_engine_mismatch")
    workload = {"prompt_tokens": len(tokens), "output_tokens": request["n_predict"],
                "context": int(args["-c"]), "batch": int(args["-b"]), "microbatch": int(args["-ub"]),
                "threads": int(args["-t"]), "gpu_layers": int(args["-ngl"]), "concurrency": 1,
                "flash_attention": args["-fa"], "cache_prompt": request["cache_prompt"],
                "temperature": request["temperature"], "seed": request["seed"], "ignore_eos": request["ignore_eos"],
                "prompt_token_ids_sha256": hashlib.sha256(encode(tokens).encode()).hexdigest()}

    def close(a, b):
        if not math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=.001):
            raise NativeError("resident_summary_or_sample_mismatch")

    def sample(row, evidence):
        timing = evidence["timings"]
        if evidence["stop_type"] != "limit" or timing.get("cache_n", 0) != 0 or timing["prompt_n"] != len(tokens) or timing["predicted_n"] != request["n_predict"]:
            raise NativeError("resident_workload_mismatch")
        close(row["prefill_tokens_per_second"], len(tokens) * 1000 / timing["prompt_ms"])
        close(row["decode_tokens_per_second"], (request["n_predict"] - 1) * 1000 / timing["predicted_ms"])
        close(timing["prompt_per_second"], row["prefill_tokens_per_second"])
        close(timing["predicted_per_second"], row["decode_tokens_per_second"])
        return {"completed_at": row["completed_at_utc"] if "completed_at_utc" in row else row["completed_at"],
                "prompt_ms": timing["prompt_ms"], "decode_ms": timing["predicted_ms"],
                "first_text_ms": float(row["ttft_ms"]), "request_wall_ms": float(row["request_wall_ms"]),
                "output_sha256": row["output_sha256"]}

    samples = []
    for number, (row, evidence) in enumerate(zip(requests, raw_timings), 1):
        if int(row["request"]) != number or evidence["request"] != number or int(row["prompt_tokens"]) != len(tokens) or int(row["generated_tokens"]) != request["n_predict"] or int(row["cached_tokens"]) != 0:
            raise NativeError("resident_sample_sequence")
        samples.append(sample(row, evidence))
    warmup_sample = sample(warmup["record"], warmup["evidence"])
    identities = {(r["gpu_uuid"], r["gpu_name"], r["driver"]) for r in telemetry}
    if len(identities) != 1:
        raise NativeError("resident_hardware_changed")
    gpu_uuid, gpu_name, driver = next(iter(identities))
    recorded_caps = "requested_power_limit_w" in telemetry[0] and "enforced_power_limit_w" in telemetry[0]
    if recorded_caps:
        cap = float(telemetry[0]["enforced_power_limit_w"])
        if declared_power_limit is not None:
            close(declared_power_limit, cap)
    else:
        cap = declared_power_limit
    readings = [{"timestamp": r["timestamp_utc"], "phase": r["phase"],
                 "elapsed_measured_seconds": float(r["elapsed_measured_seconds"]) if r["elapsed_measured_seconds"] else None,
                 **{k: float(r[k]) for k in ("temperature_c", "power_w", "utilization_percent", "memory_used_mib")},
                 "requested_power_limit_w": float(r["requested_power_limit_w"]) if recorded_caps else None,
                 "enforced_power_limit_w": float(r["enforced_power_limit_w"]) if recorded_caps else None}
                for r in telemetry]
    target = metadata["target_measured_seconds"]
    close(started["duration_seconds"], target)
    if summary["reason"] not in {"completed_600_seconds", "temperature_cutoff"}:
        raise NativeError("unsupported_resident_outcome")
    complete = summary["reason"] == "completed_600_seconds"
    observed = target if complete else max(r["elapsed_measured_seconds"] for r in readings if r["phase"] == "measured")
    artifacts = metadata.get("model_artifacts")
    if artifacts is None:
        artifacts = [{"sha256": metadata["model_sha256"], "bytes": metadata["model_bytes"]}]
    else:
        artifacts = [{"sha256": a["sha256"], "bytes": a["bytes"]} for a in artifacts]
    data = {"run_id": str(uuid5(NAMESPACE_URL, "p100-resident:" + hashlib.sha256(encode(hashes).encode()).hexdigest())),
            "engine": engine, "engine_revision": metadata["engine_revision"], "binary_sha256": metadata["binary_sha256"],
            "model_id": model_id, "model_license": model_license, "model_artifacts": artifacts,
            "model_repository_revision": metadata.get("model_repository_revision"),
            "gpu_uuid": gpu_uuid, "gpu_name": gpu_name, "driver_version": driver, "workload": workload,
            "started_at": started["timestamp_utc"], "finished_at": summary["finished_at_utc"],
            "target_seconds": target, "observed_seconds": observed, "outcome": "completed" if complete else "temperature_cutoff",
            "cutoff_c": metadata["cutoff_c"], "declared_power_limit_w": cap,
            "power_limit_evidence": "sampled" if recorded_caps else ("unknown" if cap is None else "operator_declared"),
            "warmup": warmup_sample, "requests": samples, "telemetry": readings, "source_sha256": hashes}
    run = MeasuredRun.model_validate_json(json.dumps(data, allow_nan=False))
    computed = run.summary()
    if summary["completed_requests"] != len(samples) or summary["measured_telemetry_samples"] != computed["telemetry"]["measured_samples"]:
        raise NativeError("resident_summary_or_sample_mismatch")
    for key, value in computed["metrics"].items():
        original_key = "ttft_ms" if key == "first_text_ms" else key
        for statistic in ("median", "min", "max"):
            close(value[statistic], summary["metrics"][original_key][statistic])
    for old, new in (("peak_temperature_c_all_phases", "peak_temperature_c"),
                     ("peak_power_w_measured", "peak_power_w"), ("mean_power_w_measured", "mean_power_w"),
                     ("peak_vram_mib_sampled", "sampled_peak_vram_mib")):
        close(summary[old], computed["telemetry"][new])
    return run
