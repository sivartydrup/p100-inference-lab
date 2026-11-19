"""Internal start gate: no native executable starts before supervisor attestation."""
import argparse
import json
import os
import select
import socket
import errno
import re
import time
from pathlib import Path


def confirm_isolation(command, environment, reference):
    """Check effective kernel state inside the unit, not just systemd properties."""
    try:
        if not isinstance(reference, dict) or set(reference) != {"user", "mnt"}:
            raise ValueError("Missing namespace reference")
        for namespace in ("user", "mnt"):
            own = os.readlink(f"/proc/self/ns/{namespace}")
            parent = reference[namespace]
            if not isinstance(parent, str) or not re.fullmatch(namespace + r":\[[0-9]+\]", parent):
                raise ValueError("Invalid namespace reference")
            if own == parent:
                raise ValueError("Shared namespace")
        status = dict(line.split(":", 1) for line in Path('/proc/self/status').read_text().splitlines() if ":" in line)
        if status.get('NoNewPrivs', '').strip() != '1' or status.get('Seccomp', '').strip() != '2':
            raise ValueError("Missing effective process restrictions")
        paths = [command[0], *environment['LD_LIBRARY_PATH'].split(':')]
        if '-m' in command:
            paths.append(command[command.index('-m') + 1])
        if not paths or any(not path or not os.statvfs(path).f_flag & os.ST_RDONLY for path in paths):
            raise ValueError("Native artifacts are not read-only")
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                connection = socket.socket(family, socket.SOCK_STREAM)
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT):
                    raise
            else:
                connection.close()
                raise ValueError("INET socket was permitted")
    except (OSError, ValueError, KeyError, IndexError):
        raise SystemExit("Native isolation not confirmed") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", required=True)
    parser.add_argument("--worker-pid", required=True, type=int)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--namespace-reference", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.name != "posix" or not hasattr(os, "pidfd_open"):
        raise SystemExit("Linux pidfd supervision is required")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not Path(command[0]).is_absolute() or Path(command[0]).name != "llama-bench":
        raise SystemExit("Invalid native command")
    environment = json.loads(args.environment)
    allowed = {"PATH", "LANG", "CUDA_DEVICE_ORDER", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "LD_LIBRARY_PATH"}
    if not isinstance(environment, dict) or set(environment) != allowed or any(not isinstance(v, str) or "\x00" in v for v in environment.values()):
        raise SystemExit("Invalid native environment")
    try:
        parent = os.pidfd_open(args.worker_pid)
    except OSError:
        raise SystemExit("Worker is no longer available")
    poller = select.poll()
    poller.register(parent, select.POLLIN)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if poller.poll(0):
                raise SystemExit("Worker exited before approval")
            try:
                approved = Path(args.gate).read_bytes() == b"approved\n"
            except FileNotFoundError:
                approved = False
            if approved:
                confirm_isolation(command, environment, json.loads(args.namespace_reference))
                # No shell. Environment was cleared by the fixed env -i wrapper.
                os.execve(command[0], command, environment)
            time.sleep(0.02)
        raise SystemExit("Native start gate expired")
    finally:
        os.close(parent)


if __name__ == "__main__":
    main()
