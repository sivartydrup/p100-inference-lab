"""Validated, terminal-only resident measurements. No process or network operations."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from . import native_store as store
from .jobs import encode
from .native_config import GPU, ID, SHA, NativeError, Strict, no_duplicate_keys

MAX_BYTES = 8 * 1024 * 1024


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("Timezone required")
        return result
    except (ValueError, TypeError):
        raise ValueError("Expected an offset-aware ISO timestamp") from None


class ModelArtifact(Strict):
    sha256: str = Field(pattern=SHA)
    bytes: int = Field(ge=1, le=2**40)


class Workload(Strict):
    prompt_tokens: int = Field(ge=1, le=8192)
    output_tokens: int = Field(ge=2, le=4096)
    context: int = Field(ge=32, le=131072)
    batch: int = Field(ge=1, le=4096)
    microbatch: int = Field(ge=1, le=4096)
    threads: int = Field(ge=1, le=64)
    gpu_layers: int = Field(ge=1, le=999)
    concurrency: Literal[1] = 1
    flash_attention: Literal["off", "on", "auto"]
    cache_prompt: Literal[False] = False
    temperature: Literal[0] = 0
    seed: int = Field(ge=0, le=2**32 - 1)
    ignore_eos: Literal[True] = True
    prompt_token_ids_sha256: str = Field(pattern=SHA)

    @model_validator(mode="after")
    def bounds(self):
        if self.microbatch > self.batch or self.prompt_tokens + self.output_tokens > self.context:
            raise ValueError("Inconsistent workload")
        return self


class RequestSample(Strict):
    completed_at: str = Field(max_length=40)
    prompt_ms: float = Field(gt=0, le=3600000)
    decode_ms: float = Field(gt=0, le=3600000)
    first_text_ms: float = Field(gt=0, le=3600000)
    request_wall_ms: float = Field(gt=0, le=3600000)
    output_sha256: str = Field(pattern=SHA)

    @field_validator("completed_at")
    @classmethod
    def timestamp(cls, value):
        instant(value)
        return value

    @model_validator(mode="after")
    def bounds(self):
        if self.first_text_ms > self.request_wall_ms or self.request_wall_ms + 5 < .99 * (self.prompt_ms + self.decode_ms):
            raise ValueError("Inconsistent latency")
        return self

    def metrics(self, workload):
        return {"prefill_tokens_per_second": workload.prompt_tokens * 1000 / self.prompt_ms,
                # llama-server excludes the first generated token from decode duration/rate.
                "decode_tokens_per_second": (workload.output_tokens - 1) * 1000 / self.decode_ms,
                "first_text_ms": self.first_text_ms, "request_wall_ms": self.request_wall_ms,
                "raw": self.model_dump()}


class Reading(Strict):
    timestamp: str = Field(max_length=40)
    phase: Literal["startup", "warmup", "measured"]
    elapsed_measured_seconds: float | None = Field(default=None, ge=0, le=3603)
    temperature_c: float = Field(ge=0, le=120)
    power_w: float = Field(ge=0, le=1000)
    utilization_percent: float = Field(ge=0, le=100)
    memory_used_mib: float = Field(ge=0, le=1048576)
    requested_power_limit_w: float | None = Field(default=None, ge=1, le=1000)
    enforced_power_limit_w: float | None = Field(default=None, ge=1, le=1000)

    @field_validator("timestamp")
    @classmethod
    def time(cls, value):
        instant(value)
        return value

    @model_validator(mode="after")
    def phase_time(self):
        if (self.phase == "measured") != (self.elapsed_measured_seconds is not None):
            raise ValueError("Measured readings require measured elapsed time")
        return self


class TestContext(Strict):
    """Operator declarations, never inferred from board telemetry."""
    session_id: UUID | None = None
    gpu_uuids: list[Annotated[str, Field(pattern=GPU)]] = Field(min_length=1, max_length=8)
    enclosure: Literal["unknown", "open", "closed"] = "unknown"

    @model_validator(mode="after")
    def participants(self):
        if len({gpu.lower() for gpu in self.gpu_uuids}) != len(self.gpu_uuids):
            raise ValueError("Duplicate session GPU")
        if len(self.gpu_uuids) > 1 and self.session_id is None:
            raise ValueError("Multiple GPUs require a session ID")
        self.gpu_uuids = sorted(self.gpu_uuids, key=str.lower)
        return self


class MeasuredRun(Strict):
    format: Literal["p100-resident-measurement-v1"] = "p100-resident-measurement-v1"
    run_id: UUID
    engine: Literal["kmic", "qr0n"]
    engine_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    binary_sha256: str = Field(pattern=SHA)
    model_id: str = Field(pattern=ID)
    model_license: str = Field(min_length=1, max_length=200)
    model_repository_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    model_artifacts: list[ModelArtifact] = Field(min_length=1, max_length=16)
    gpu_uuid: str = Field(pattern=GPU)
    gpu_name: str = Field(min_length=1, max_length=128)
    driver_version: str = Field(pattern=r"^[0-9.]{3,32}$")
    workload: Workload
    started_at: str = Field(max_length=40)
    finished_at: str = Field(max_length=40)
    target_seconds: float = Field(ge=1, le=3600)
    observed_seconds: float = Field(gt=0, le=3600)
    outcome: Literal["completed", "temperature_cutoff"]
    cutoff_c: float = Field(ge=30, le=90)
    declared_power_limit_w: float | None = Field(default=None, ge=1, le=1000)
    power_limit_evidence: Literal["sampled", "operator_declared", "unknown"]
    warmup: RequestSample
    requests: list[RequestSample] = Field(min_length=1, max_length=1000)
    telemetry: list[Reading] = Field(min_length=1, max_length=3600)
    source_sha256: dict[str, str] = Field(min_length=1, max_length=12)
    test_context: TestContext | None = None

    @field_validator("model_license", "gpu_name")
    @classmethod
    def clean_label(cls, value):
        if any(c in value for c in "\r\n\x00/\\"):
            raise ValueError("Labels must not contain paths or control characters")
        return value

    @field_validator("source_sha256")
    @classmethod
    def source_hashes(cls, value):
        import re
        for name, digest in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}\.(?:json|jsonl|csv)", name) or not re.fullmatch(SHA, digest):
                raise ValueError("Expected relative evidence filenames and SHA-256 hashes")
        return value

    @model_validator(mode="after")
    def consistency(self):
        if self.test_context and self.gpu_uuid.lower() not in {gpu.lower() for gpu in self.test_context.gpu_uuids}:
            raise ValueError("Measured GPU must be a session participant")
        start, end = instant(self.started_at), instant(self.finished_at)
        if self.observed_seconds > self.target_seconds or end < start + timedelta(seconds=self.observed_seconds - .1):
            raise ValueError("Inconsistent observation window")
        if self.outcome == "completed" and self.observed_seconds != self.target_seconds:
            raise ValueError("Completed runs require the full window")
        previous = start
        limit = start + timedelta(seconds=self.observed_seconds + .1)
        for request in self.requests:
            completed = instant(request.completed_at)
            if not previous < completed <= min(end, limit):
                raise ValueError("Requests must be ordered inside the measured window")
            previous = completed
        if self.warmup and instant(self.warmup.completed_at) > start:
            raise ValueError("Warmup must finish before measurement starts")
        measured = []
        previous_time = None
        previous_elapsed = -1
        for reading in self.telemetry:
            stamp = instant(reading.timestamp)
            if stamp > end or (previous_time is not None and stamp <= previous_time):
                raise ValueError("Telemetry timestamps must be ordered")
            previous_time = stamp
            if reading.phase == "measured":
                elapsed = reading.elapsed_measured_seconds
                if not previous_elapsed < elapsed <= self.observed_seconds + .1:
                    raise ValueError("Invalid measured telemetry time")
                if abs((stamp - start).total_seconds() - elapsed) > 3:
                    raise ValueError("Telemetry clocks disagree")
                previous_elapsed = elapsed
                measured.append(reading)
            elif stamp > start:
                raise ValueError("Startup/warmup telemetry must precede measurement")
            caps = (reading.requested_power_limit_w, reading.enforced_power_limit_w)
            if self.power_limit_evidence == "sampled":
                if self.declared_power_limit_w is None or any(v != self.declared_power_limit_w for v in caps):
                    raise ValueError("Power cap is not confirmed in every reading")
            elif any(v is not None for v in caps):
                raise ValueError("Power readings require sampled-cap evidence")
        if not measured:
            raise ValueError("Measured telemetry required")
        if self.power_limit_evidence == "unknown" and self.declared_power_limit_w is not None:
            raise ValueError("Unknown power cap must be null")
        if self.power_limit_evidence == "operator_declared" and self.declared_power_limit_w is None:
            raise ValueError("Declared cap required")
        maximum = max(r.temperature_c for r in self.telemetry)
        if self.outcome == "completed" and maximum >= self.cutoff_c:
            raise ValueError("Thermal cutoff cannot be a completed run")
        if self.outcome == "temperature_cutoff" and (maximum < self.cutoff_c or abs(measured[-1].elapsed_measured_seconds - self.observed_seconds) > .1):
            raise ValueError("Thermal cutoff requires a matching final observation")
        if self.outcome == "temperature_cutoff" and (
            self.telemetry[-1].phase != "measured" or self.telemetry[-1].temperature_c < self.cutoff_c
            or any(r.temperature_c >= self.cutoff_c for r in self.telemetry[:-1])
        ):
            raise ValueError("Thermal cutoff must stop at the first matching reading")
        return self

    def digest(self):
        data = self.model_dump(mode="json")
        # Preserve the exact v1 fingerprint for archived imports without context.
        if self.test_context is None:
            data.pop("test_context")
        return hashlib.sha256(encode(data).encode()).hexdigest()

    def provenance(self):
        fields = ("engine", "engine_revision", "binary_sha256", "model_id", "model_license",
                  "model_repository_revision", "model_artifacts", "gpu_uuid", "gpu_name", "driver_version",
                  "workload", "target_seconds", "observed_seconds", "outcome", "cutoff_c",
                  "declared_power_limit_w", "power_limit_evidence", "source_sha256")
        data = self.model_dump(mode="json", include=set(fields))
        if self.test_context:
            data["test_context"] = self.test_context.model_dump(mode="json")
            data["context_assurance"] = "Operator-declared session participants and enclosure; not independently attested"
        comparable = {k: data[k] for k in ("model_artifacts", "gpu_uuid", "driver_version", "workload")}
        workload = {k: data[k] for k in ("model_artifacts", "driver_version", "workload")}
        data["session_workload_key"] = hashlib.sha256(encode(workload).encode()).hexdigest()
        data.update({"record_origin": "operator_import", "protocol": self.format,
                     "comparison_key": hashlib.sha256(encode(comparable).encode()).hexdigest(),
                     "measurement_sha256": self.digest(), "warmup_excluded": self.warmup is not None,
                     "first_text_definition": "Request start to first nonempty streamed text over a local Unix socket; not exact raw-token TTFT",
                     "assurance": "Operator-imported measurements, not independently attested native-runner execution"})
        return data

    def summary(self):
        values = [r.metrics(self.workload) for r in self.requests]
        measured = [r for r in self.telemetry if r.phase == "measured"]
        return {"source": "native", "sample_count": len(values), "partial": self.outcome != "completed",
                "ttft_ms": None,
                "metrics": {k: {"median": statistics.median(s[k] for s in values),
                                "min": min(s[k] for s in values), "max": max(s[k] for s in values),
                                "stddev": statistics.pstdev(s[k] for s in values)}
                            for k in ("prefill_tokens_per_second", "decode_tokens_per_second", "first_text_ms", "request_wall_ms")},
                "telemetry": {"measured_samples": len(measured),
                              "peak_temperature_c": max(r.temperature_c for r in self.telemetry),
                              "mean_power_w": statistics.mean(r.power_w for r in measured),
                              "peak_power_w": max(r.power_w for r in measured),
                              "sampled_peak_vram_mib": max(r.memory_used_mib for r in measured)},
                "notice": "Resident streaming workload; warmup excluded. Sampled peaks, not continuous maxima. Partial results are not completed-window acceptance."}


def load_run(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise NativeError("invalid_measurement_file")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise NativeError("invalid_measurement_file")
    # Validate JSON-specific UUID coercion while retaining strict numeric/boolean validation.
    data = json.loads(raw, object_pairs_hook=no_duplicate_keys,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    return MeasuredRun.model_validate_json(json.dumps(data, allow_nan=False))


def import_run(path, run):
    """Append one immutable terminal record; never enqueue work or execute anything."""
    job_id = str(run.run_id)
    key = "import:" + job_id
    with store.connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM native_jobs WHERE request_key=?", (key,)).fetchone()
        if prior:
            if prior["preset_hash"] != run.digest():
                raise NativeError("idempotency_conflict")
            return store.public(prior)
        status = "succeeded" if run.outcome == "completed" else "failed"
        error = None if run.outcome == "completed" else run.outcome
        start_utc = instant(run.started_at).astimezone(timezone.utc).isoformat()
        end_utc = instant(run.finished_at).astimezone(timezone.utc).isoformat()
        c.execute("""INSERT INTO native_jobs(id,request_key,preset_id,preset_hash,provenance_json,status,
                  created_at,started_at,finished_at,error_code,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (job_id, key, "import-" + run.run_id.hex, run.digest(), encode(run.provenance()), status,
                   start_utc, start_utc, end_utc, error, encode(run.summary())))
        samples = [("measured", i, s.metrics(run.workload)) for i, s in enumerate(run.requests)]
        if run.warmup:
            samples.append(("warmup", 0, run.warmup.metrics(run.workload)))
        samples.extend(("telemetry", i, s.model_dump()) for i, s in enumerate(run.telemetry))
        c.executemany("INSERT INTO native_attempts VALUES(?,?,?,?,?)",
                      [(job_id, phase, i, f"import:{job_id}:{phase}:{i}", encode(value)) for phase, i, value in samples])
        store.event(c, job_id, "terminal_measurement_imported_by_local_operator")
        return store.public(c.execute("SELECT * FROM native_jobs WHERE id=?", (job_id,)).fetchone())


def publish_snapshot(source, directory):
    """Consistent standalone backup, atomically replaced in a directory mounted read-only."""
    source, directory = Path(source).resolve(), Path(directory)
    if directory.is_symlink():
        raise NativeError("unsafe_snapshot_directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = directory / "native.sqlite3"
    if final.resolve() == source or final.is_symlink():
        raise NativeError("unsafe_snapshot_destination")
    if final.exists():
        # Never overwrite unrelated data; validate using the read-only identity guard.
        with store.connect(final, readonly=True):
            pass
    descriptor, temporary = tempfile.mkstemp(prefix=".native-", suffix=".sqlite3", dir=directory)
    os.close(descriptor)
    try:
        with store.connect(source, readonly=True) as src, closing(sqlite3.connect(temporary)) as dst:
            deadline = time.monotonic() + 30
            def progress(*_):
                if time.monotonic() >= deadline:
                    raise NativeError("snapshot_deadline_exceeded")
            src.backup(dst, pages=256, progress=progress, sleep=.05)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        # Directory access is restricted separately; stable read mode survives replacement.
        os.chmod(temporary, 0o644)
        os.replace(temporary, final)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return final
