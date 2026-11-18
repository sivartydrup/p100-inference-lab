"""Entirely synthetic imported measurements; not real hardware performance."""
def measurement():
    request = {"completed_at": "2026-01-01T00:00:03Z", "prompt_ms": 1000.0,
               "decode_ms": 1000.0, "first_text_ms": 1100.0, "request_wall_ms": 2001.0,
               "output_sha256": "e" * 64}
    warmup = {**request, "completed_at": "2025-12-31T23:59:59Z"}
    telemetry = {"timestamp": "2026-01-01T00:00:00.5Z", "phase": "measured", "elapsed_measured_seconds": .5,
                 "temperature_c": 50.0, "power_w": 149.0, "utilization_percent": 99.0, "memory_used_mib": 4000.0,
                 "requested_power_limit_w": 150.0, "enforced_power_limit_w": 150.0}
    return {"format": "p100-resident-measurement-v1", "run_id": "11111111-1111-4111-8111-111111111111",
            "engine": "kmic", "engine_revision": "a" * 40, "binary_sha256": "b" * 64,
            "model_id": "synthetic-model", "model_license": "Synthetic fixture only",
            "model_artifacts": [{"sha256": "c" * 64, "bytes": 1000}],
            "gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111", "gpu_name": "Synthetic test GPU",
            "driver_version": "123.45", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:10.2Z",
            "target_seconds": 10.0, "observed_seconds": 10.0, "outcome": "completed", "cutoff_c": 75.0,
            "declared_power_limit_w": 150.0, "power_limit_evidence": "sampled", "warmup": warmup,
            "requests": [request, {**request, "completed_at": "2026-01-01T00:00:06Z", "decode_ms": 2000.0, "request_wall_ms": 3001.0}],
            "telemetry": [telemetry, {**telemetry, "timestamp": "2026-01-01T00:00:09.5Z", "elapsed_measured_seconds": 9.5,
                                       "temperature_c": 51.0, "power_w": 151.0}],
            "workload": {"prompt_tokens": 512, "output_tokens": 256, "context": 2048, "batch": 512,
                         "microbatch": 128, "threads": 4, "gpu_layers": 99, "concurrency": 1,
                         "flash_attention": "off", "cache_prompt": False, "temperature": 0, "seed": 42,
                         "ignore_eos": True, "prompt_token_ids_sha256": "d" * 64},
            "source_sha256": {"requests.csv": "f" * 64}}
