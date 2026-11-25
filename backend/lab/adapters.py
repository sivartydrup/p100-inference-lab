"""Local native Ubuntu adapters. No SSH client or shell interpretation."""
import csv
import io
import subprocess
from pathlib import Path

ENGINE_IDS = ("kmic", "kmic-moe", "pxa", "qr0n", "strata", "scream", "scream-512k", "inkling")
MANAGED_UNITS = {"kmic": "kmic-qwen27b.service",
                 **{engine: f"p100-lab-{engine}.service" for engine in ENGINE_IDS if engine != "kmic"}}


def run(argv: list[str], timeout: int = 5):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


class NativeAdapter:
    def __init__(self, engine_id: str):
        if engine_id not in ENGINE_IDS:
            raise ValueError("Unknown engine")
        self.id = engine_id
        self.unit = MANAGED_UNITS[engine_id]

    def status(self):
        try:
            result = run(["systemctl", "--user", "is-active", self.unit])
            state = result.stdout.strip()
            if state not in {"active", "inactive", "failed", "activating", "deactivating"}:
                state = "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            state = "unavailable"
        return {"id": self.id, "unit": self.unit, "state": state,
                "note": "Service state only; inference readiness is not probed."}



def gpu_metrics():
    try:
        result = run(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,temperature.gpu,power.draw,utilization.gpu", "--format=csv,noheader,nounits"])
        if result.returncode:
            return {"available": False, "devices": [], "message": "NVIDIA telemetry unavailable"}
        def number(value):
            try:
                return float(value.strip())
            except ValueError:
                return None
        devices = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) == 7:
                devices.append(dict(zip(("index", "name", "memory_total_mib", "memory_used_mib", "temperature_c", "power_w", "utilization_percent"),
                                        [row[0].strip(), row[1].strip(), *map(number, row[2:])])))
        return {"available": True, "devices": devices, "message": None}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "devices": [], "message": "nvidia-smi not installed or timed out"}


def inventory(roots: list[str], limit: int = 1000):
    """Bounded walk; never follow symlinks or return absolute host paths."""
    import os
    items, warnings = [], []
    visited = 0
    for root_index, configured in enumerate(roots):
        root = Path(configured)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            warnings.append(f"Model root {root_index + 1} is unavailable")
            continue
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
            visited += 1
            if visited > 5000:
                return {"items": items, "warnings": warnings + ["Directory scan limit reached"], "truncated": True}
            for name in sorted(names):
                p = Path(directory) / name
                if p.suffix.lower() not in {".gguf", ".safetensors"} or p.is_symlink():
                    continue
                try:
                    stat = p.stat()
                    items.append({"root": root_index + 1, "name": p.relative_to(root).as_posix(), "bytes": stat.st_size})
                except OSError:
                    warnings.append("A model file could not be read")
                if len(items) >= limit:
                    return {"items": items, "warnings": warnings, "truncated": True}
    return {"items": items, "warnings": warnings, "truncated": False}
