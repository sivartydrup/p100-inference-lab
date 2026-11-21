"""Host-only launcher for a sealed catalog and its private staged selection.

Install in reviewed, root-owned code. Never called by the HTTP process.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat

from .kmic_profiles import FLASH, FLASH_BINARY_SHA256, LEGACY, launch_arguments
from .managed_models import ManagedModels, path_value


def prepared_command(config, binary, host, port, key_file):
    """Pure preparation apart from artifact/stat checks; no subprocess or shell."""
    models = ManagedModels(config["managed_models"])
    selection = models.read()
    entry = models.selection(**selection, require_available=True)
    binary = path_value(str(binary))
    key_file = path_value(str(key_file))
    address = ipaddress.IPv4Address(host)
    if not (address.is_private or address.is_loopback) or not 1024 <= port <= 65535:
        raise ValueError("Expected the reviewed local listener")
    if str(address) != host or binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Expected a regular executable at the pinned path")
    runtime = entry.get("runtime", LEGACY)
    if runtime in FLASH and hashlib.sha256(binary.read_bytes()).hexdigest() != FLASH_BINARY_SHA256:
        raise ValueError("Flash Next requires the benchmarked Kmic binary; revalidate a new build")
    args = [str(binary), *launch_arguments(entry, selection["settings"]),
            "--host", host, "--port", str(port), "--api-key-file", str(key_file)]
    # Remove engine option overrides inherited from a login/session environment.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("LLAMA_", "GGML_", "CUDA_"))}
    env.update(CUDA_VISIBLE_DEVICES="0,1", GGML_CUDA_P2P="1", GGML_CUDA_GRAPHS_PRE_VOLTA="3")
    if runtime == LEGACY:
        env.update(LLAMA_SPEC_SAMPLE_TEMP="1.0", LLAMA_SPEC_DRAFT_TOPK="20")
    return args, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    args = parser.parse_args()
    if os.getuid() == 0:
        raise SystemExit("Run as the existing non-root engine account")
    info = args.config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit("Expected a sealed root-owned agent configuration")
    try:
        command, env = prepared_command(json.loads(args.config.read_text()), args.binary, args.host, args.port, args.api_key_file)
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("Approved engine profile unavailable; no engine launched") from None
    # Replace this process, so the existing systemd cgroup, stop and readiness semantics remain in force.
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
