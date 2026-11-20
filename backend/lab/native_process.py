"""Bounded subprocess IO and uniquely named systemd user-unit supervision."""
import os
import json
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass
from uuid import UUID

from .native_config import NativeError

OUTPUT_LIMIT = 262144
UNIT = re.compile(r"^p100-lab-bench-[0-9a-f-]{36}-(?:warmup|measured)-[0-9]{1,2}\.service$")


def unit_name(job_id, phase, trial):
    if phase not in {"warmup", "measured"} or not 0 <= trial < 12:
        raise NativeError("invalid_unit_identity")
    return f"p100-lab-bench-{UUID(job_id)}-{phase}-{trial}.service"


@dataclass
class Captured:
    returncode: int
    stdout: bytes
    stderr: bytes
    wall_ms: float


def capture(argv, env, deadline, check=lambda: None, limit=OUTPUT_LIMIT):
    """Linux-only pipe draining; never buffer more than the combined output cap."""
    if os.name != "posix":
        raise NativeError("linux_required")
    check()
    if time.monotonic() >= deadline:
        raise NativeError("deadline_exceeded")
    started = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, start_new_session=True, shell=False)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    complete = False
    selector = selectors.DefaultSelector()
    try:
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() or process.poll() is None:
            check()
            if time.monotonic() >= deadline:
                raise NativeError("deadline_exceeded")
            for key, _ in selector.select(0.05):
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if sum(map(len, buffers.values())) + len(data) > limit:
                    raise NativeError("output_limit_exceeded")
                buffers[key.data].extend(data)
        complete = True
        return Captured(process.wait(timeout=1), bytes(buffers["stdout"]), bytes(buffers["stderr"]),
                        (time.monotonic() - started) * 1000)
    finally:
        selector.close()
        if not complete:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            # Descendants may hold pipes even after the direct child exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


class SystemdSupervisor:
    def namespace_reference(self):
        return {name: os.readlink(f"/proc/self/ns/{name}") for name in ("user", "mnt")}

    def __init__(self):
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            raise NativeError("non_root_linux_user_required")
        self.env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                    "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus"}

    def control(self, *args):
        return capture(["/usr/bin/systemctl", "--user", "--no-ask-password", *args], self.env,
                       time.monotonic() + 5, limit=65536)

    def available(self):
        result = self.control("show", "--property=Version", "--value")
        if result.returncode or not result.stdout.strip():
            raise NativeError("user_systemd_unavailable")

    def listed_units(self):
        result = self.control("list-units", "--all", "--no-legend", "--plain", "--type=service", "p100-lab-bench-*.service")
        if result.returncode:
            raise NativeError("unit_inventory_failed")
        names = set()
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            if not line.strip():
                continue
            name = line.split()[0]
            if not UNIT.fullmatch(name):
                raise NativeError("unknown_native_unit")
            names.add(name)
        return names

    def stop(self, unit):
        if not UNIT.fullmatch(unit):
            raise NativeError("invalid_unit_identity")
        # Do not accept a failed stop as proof that the process tree is gone.
        try:
            self.control("stop", unit)
            state = self.control("show", unit, "--property=LoadState", "--property=ActiveState")
        except (OSError, NativeError):
            raise NativeError("cleanup_unconfirmed")
        values = dict(line.split("=", 1) for line in state.stdout.decode().splitlines() if "=" in line)
        missing = values.get("LoadState") == "not-found" and values.get("ActiveState") == "inactive"
        inactive = state.returncode == 0 and values.get("ActiveState") in {"inactive", "failed"}
        if not (missing or inactive):
            raise NativeError("cleanup_unconfirmed")

    def command(self, preset, unit, runtime_seconds, gate):
        if not UNIT.fullmatch(unit):
            raise NativeError("invalid_unit_identity")
        properties = ["KillMode=control-group", "SendSIGKILL=yes", "TimeoutStopSec=2",
                      f"RuntimeMaxSec={runtime_seconds:.3f}", f"MemoryMax={preset.memory_max_mib}M",
                      "TasksMax=64", f"CPUQuota={preset.threads * 100}%", "NoNewPrivileges=yes",
                      "PrivateUsers=yes", "PrivateTmp=yes", "ProtectSystem=strict", "ProtectHome=read-only",
                      "RestrictAddressFamilies=AF_UNIX", "UMask=0077", "WorkingDirectory=/"]
        return ["/usr/bin/systemd-run", "--user", "--no-ask-password", "--quiet", "--wait", "--pipe",
                "--collect", "--service-type=exec", "--expand-environment=no", f"--unit={unit}",
                *["--property=" + p for p in properties], "--", "/usr/bin/env", "-i",
                "PATH=/usr/bin:/bin", "LANG=C.UTF-8", sys.executable,
                str(Path(__file__).with_name("native_child.py")), "--gate", str(gate),
                "--worker-pid", str(os.getpid()), "--environment", json.dumps(preset.environment()),
                "--namespace-reference", json.dumps(self.namespace_reference()),
                "--", *preset.argv()]

    def attest(self, unit, preset, remaining):
        properties = ("LoadState", "ActiveState", "KillMode", "SendSIGKILL", "NoNewPrivileges",
                      "PrivateUsers", "PrivateTmp", "ProtectSystem", "ProtectHome", "RestrictAddressFamilies",
                      "MemoryMax", "TasksMax", "RuntimeMaxUSec", "CPUQuotaPerSecUSec")
        result = self.control("show", unit, *["--property=" + p for p in properties])
        values = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
        if values.get("ActiveState") in {"activating", "inactive"} or values.get("LoadState") == "not-found":
            return False
        required = {"LoadState": "loaded", "ActiveState": "active", "KillMode": "control-group",
                    "SendSIGKILL": "yes", "NoNewPrivileges": "yes", "PrivateUsers": "yes", "PrivateTmp": "yes",
                    "ProtectSystem": "strict", "ProtectHome": "read-only", "RestrictAddressFamilies": "AF_UNIX"}
        try:
            valid = result.returncode == 0 and all(values.get(k) == v for k, v in required.items())
            valid &= 0 < int(values["MemoryMax"]) <= preset.memory_max_mib * 2**20
            valid &= 0 < int(values["TasksMax"]) <= 64
            valid &= 0 < duration_seconds(values["RuntimeMaxUSec"]) <= remaining + 0.01
            valid &= 0 < duration_seconds(values["CPUQuotaPerSecUSec"]) <= preset.threads
        except (KeyError, ValueError):
            valid = False
        if not valid:
            raise NativeError("unit_policy_not_confirmed")
        return True

    def run(self, preset, unit, deadline, check):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeError("deadline_exceeded")
        # A late D-Bus unit creation can never execute a benchmark without this gate.
        with tempfile.TemporaryDirectory(prefix="p100-bench-gate-", dir=self.env["XDG_RUNTIME_DIR"]) as directory:
            gate = Path(directory) / "approved"
            released = False
            def guarded_check():
                nonlocal released
                check()
                if not released and self.attest(unit, preset, remaining):
                    check()
                    if time.monotonic() >= deadline:
                        raise NativeError("deadline_exceeded")
                    with gate.open("xb") as stream:
                        stream.write(b"approved\n")
                    released = True
            result = capture(self.command(preset, unit, remaining, gate), self.env, deadline, guarded_check)
            if not released:
                raise NativeError("native_start_not_approved")
            return result


def duration_seconds(value):
    units = {"us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "h": 3600}
    pieces = value.split()
    if not pieces:
        raise ValueError("Missing duration")
    total = 0
    for piece in pieces:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s|min|h)", piece)
        if not match:
            raise ValueError("Unsupported duration")
        total += float(match[1]) * units[match[2]]
    return total
