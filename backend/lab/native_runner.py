"""Opt-in, host-only CLI microbenchmarks, supervised by systemd user units."""
import csv
import hashlib
import io
import os
import threading
import time
from pathlib import Path

import psutil

from . import native_store as store
from .native_config import NativeError, unchanged, verify_artifacts
from .native_process import capture, unit_name
from .native_protocol import parse_output, summarize
from .worker import worker_lock


class NvidiaHardware:
    def query(self, fields, kind, check):
        result = capture(["/usr/bin/nvidia-smi", f"--query-{kind}=" + fields,
                          "--format=csv,noheader,nounits"], {"PATH": "/usr/bin:/bin", "LANG": "C"},
                         time.monotonic() + 5, check, limit=65536)
        if result.returncode:
            raise NativeError("nvidia_telemetry_unavailable")
        return list(csv.reader(io.StringIO(result.stdout.decode("utf-8"))))

    def snapshot(self, preset, check):
        rows = self.query("uuid,name,driver_version,compute_cap,memory.total,memory.free,temperature.gpu,power.draw,utilization.gpu", "gpu", check)
        selected = [r for r in rows if r and r[0].strip() == preset.gpu_uuid]
        if len(selected) != 1 or len(selected[0]) != 9:
            raise NativeError("selected_gpu_unavailable")
        r = [s.strip() for s in selected[0]]
        try:
            total, free, temperature = map(float, r[4:7])
            if not all(0 <= v < 2**30 for v in (total, free, temperature)) or free > total:
                raise ValueError
        except ValueError:
            raise NativeError("invalid_gpu_telemetry")
        def optional(value):
            try:
                number = float(value)
                return number if 0 <= number < 1e9 else None
            except ValueError:
                return None
        return {"uuid": r[0], "name": r[1][:128], "driver_version": r[2],
                "compute_capability": r[3], "total_vram_mib": total, "free_vram_mib": free,
                "temperature_c": temperature, "power_w": optional(r[7]),
                "utilization_percent": optional(r[8])}

    def preflight(self, preset, db, check):
        gpu = self.snapshot(preset, check)
        if gpu["driver_version"] != preset.driver_version or gpu["compute_capability"] != preset.compute_capability:
            raise NativeError("gpu_or_driver_mismatch")
        if gpu["free_vram_mib"] < preset.min_free_vram_mib or gpu["temperature_c"] > preset.max_temperature_c:
            raise NativeError("gpu_resource_preflight_failed")
        if psutil.virtual_memory().available < preset.min_free_ram_mib * 2**20:
            raise NativeError("host_ram_preflight_failed")
        if psutil.disk_usage(str(Path(db).parent)).free < 256 * 2**20 or preset.threads > (os.cpu_count() or 1):
            raise NativeError("host_resource_preflight_failed")
        for row in self.query("gpu_uuid,pid", "compute-apps", check):
            if len(row) != 2:
                raise NativeError("invalid_gpu_process_telemetry")
            if row[0].strip() == preset.gpu_uuid:
                raise NativeError("selected_gpu_busy")
        return gpu


def recover(db, supervisor):
    """Fail closed on unknown/uncleanable units before claiming any queued work."""
    supervisor.available()
    with store.connect(db, readonly=True) as c:
        known = {r["unit"] for r in c.execute("SELECT unit FROM native_attempts")}
    listed = supervisor.listed_units()
    if listed - known:
        raise NativeError("unknown_native_unit_active")
    for unit in listed:
        supervisor.stop(unit)
    for job in store.active_jobs(db):
        detail = store.get_job(db, job["id"])
        try:
            for attempt in detail["attempts"]:
                expected = unit_name(job["id"], attempt["phase"], attempt["trial"])
                if attempt["unit"] != expected:
                    raise NativeError("invalid_unit_identity")
                supervisor.stop(expected)
        except NativeError:
            store.finish(db, job["id"], "cleanup_failed", "cleanup_unconfirmed")
            raise
        store.finish(db, job["id"], "interrupted", "worker_restarted")


def execute(db, job, registry, supervisor, hardware, stop):
    preset = registry.approved(job["preset_id"])
    if preset.fingerprint() != job["preset_hash"]:
        store.finish(db, job["id"], "failed", "preset_changed")
        return
    deadline = time.monotonic() + preset.job_timeout_seconds
    samples = []
    current_unit = None

    def check():
        if stop.is_set():
            raise NativeError("worker_stopped")
        if store.cancellation_requested(db, job["id"]):
            raise NativeError("cancelled")
        if time.monotonic() >= deadline:
            raise NativeError("deadline_exceeded")

    try:
        identities = verify_artifacts(preset, check)
        for phase, count in (("warmup", preset.warmup), ("measured", preset.trials)):
            for trial in range(count):
                check()
                unchanged(identities)
                before = hardware.preflight(preset, db, check)
                check()
                unit = unit_name(job["id"], phase, trial)
                store.plan(db, job["id"], phase, trial, unit)
                current_unit = unit
                result = supervisor.run(preset, unit, min(deadline, time.monotonic() + preset.trial_timeout_seconds), check)
                supervisor.stop(unit)
                current_unit = None
                check()
                if result.returncode:
                    raise NativeError("engine_exit_nonzero")
                unchanged(identities)
                value = parse_output(result.stdout, preset)
                value.update({"source": "native", "process_wall_ms": result.wall_ms,
                              "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
                              "gpu_before": before, "gpu_after": hardware.snapshot(preset, check)})
                check()
                store.sample(db, job["id"], phase, trial, value)
                if phase == "measured":
                    samples.append(value)
        check()
        store.finish(db, job["id"], "succeeded", summary=summarize(samples))
    except Exception as error:
        if current_unit is not None:
            try:
                supervisor.stop(current_unit)
            except Exception:
                store.finish(db, job["id"], "cleanup_failed", "cleanup_unconfirmed")
                raise NativeError("cleanup_unconfirmed")
        code = error.code if isinstance(error, NativeError) else "native_worker_error"
        status = {"cancelled": "cancelled", "worker_stopped": "interrupted",
                  "deadline_exceeded": "timed_out"}.get(code, "failed")
        store.finish(db, job["id"], status, code)


def runtime_lock_path():
    if os.name != "posix" or os.getuid() == 0:
        raise NativeError("non_root_linux_user_required")
    runtime = Path(f"/run/user/{os.getuid()}")
    info = runtime.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise NativeError("unsafe_runtime_directory")
    return runtime / "p100-lab-native-global"


def serve(db, registry, supervisor, hardware, stop, once=False):
    # Global per-user exclusion covers workers pointed at different databases.
    with worker_lock(runtime_lock_path()), worker_lock(Path(db)):
        recover(db, supervisor)
        while not stop.is_set():
            job = store.claim(db)
            if job:
                try:
                    execute(db, job, registry, supervisor, hardware, stop)
                except NativeError as error:
                    if error.code == "cleanup_unconfirmed":
                        raise
                    store.finish(db, job["id"], "failed", error.code)
            if once:
                return
            if job is None:
                stop.wait(0.25)
