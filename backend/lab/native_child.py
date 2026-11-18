"""Internal start gate: no native executable starts before supervisor attestation."""
import argparse
import json
import os
import select
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", required=True)
    parser.add_argument("--worker-pid", required=True, type=int)
    parser.add_argument("--environment", required=True)
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
                # No shell. Environment was cleared by the fixed env -i wrapper.
                os.execve(command[0], command, environment)
            time.sleep(0.02)
        raise SystemExit("Native start gate expired")
    finally:
        os.close(parent)


if __name__ == "__main__":
    main()
