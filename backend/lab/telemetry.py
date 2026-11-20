"""Read-only samples. Mock values never represent measured performance."""
import os
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .adapters import gpu_metrics
from .fans import fan_metrics


def sample(mode: str, disk_path: Path):
    if mode == "mock":
        return {
            "mode": "mock", "scope": "Simulated Windows development host",
            "cpu_percent": 12.5,
            "memory": {"total": 64 * 2**30, "used": 16 * 2**30, "percent": 25},
            "disk": {"total": 1000 * 2**30, "used": 320 * 2**30, "percent": 32},
            "fans": {"available": True, "source": "mock", "controls_enabled": False,
                     "partial": False, "truncated": False,
                     "message": "Simulated fan readings; not measured hardware data.",
                     "devices": [{"id": "mock:fan1", "controller": "Simulated controller",
                                  "device": "mock", "channel": "fan1", "rpm": 4200},
                                 {"id": "mock:fan2", "controller": "Simulated controller",
                                  "device": "mock", "channel": "fan2", "rpm": 4250}]},
            "gpus": {"available": True, "message": "Simulated GPU; not a benchmark",
                     "devices": [{"index": "0", "name": "Tesla P100 (MOCK)",
                                  "memory_total_mib": 16384, "memory_used_mib": 2048,
                                  "temperature_c": 38, "power_w": 45,
                                  "utilization_percent": 10}]},
            "sampled_at": datetime.now(timezone.utc).isoformat(),
        }
    # Linux Compose mounts host proc read-only. No host PID namespace is needed.
    psutil.PROCFS_PATH = os.getenv("LAB_PROCFS", "/proc")
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(disk_path.resolve()))
    return {
        "mode": "live", "scope": "Host CPU/RAM; database-volume filesystem",
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {"total": memory.total, "used": memory.used, "percent": memory.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "gpus": gpu_metrics(), "fans": fan_metrics(),
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }
