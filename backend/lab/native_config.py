"""Administrator-only native presets. No browser-supplied commands or flags."""
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .jobs import encode

ID = r"^[a-z0-9][a-z0-9-]{0,63}$"
SHA = r"^[a-f0-9]{64}$"
GPU = r"^GPU-[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$"


class NativeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Artifact(Strict):
    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=SHA)

    @model_validator(mode="after")
    def absolute_regular_path(self):
        if not PurePosixPath(self.path).is_absolute() or any(c in self.path for c in "\n\r\x00:$%"):
            raise ValueError("Artifacts must use absolute paths without expansion characters")
        return self


class NativePreset(Strict):
    id: str = Field(pattern=ID)
    reviewed: bool = False
    engine: Literal["kmic", "qr0n"]
    engine_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    protocol: Literal["llama-bench-json-v1"] = "llama-bench-json-v1"
    binary: Artifact
    libraries: list[Artifact] = Field(min_length=1, max_length=16)
    model_id: str = Field(pattern=ID)
    model: Artifact
    model_license: str = Field(min_length=1, max_length=200)
    declared_cuda_version: str = Field(min_length=1, max_length=40)
    gpu_uuid: str = Field(pattern=GPU)
    compute_capability: str = Field(pattern=r"^[0-9]{1,2}\.[0-9]$")
    driver_version: str = Field(pattern=r"^[0-9.]{3,32}$")
    min_free_vram_mib: int = Field(ge=256, le=1048576)
    min_free_ram_mib: int = Field(ge=256, le=1048576)
    max_temperature_c: int = Field(default=80, ge=30, le=90)
    memory_max_mib: int = Field(ge=256, le=1048576)
    threads: int = Field(default=4, ge=1, le=64)
    prompt_tokens: int = Field(default=128, ge=16, le=8192)
    output_tokens: int = Field(default=32, ge=16, le=1024)
    batch: int = Field(default=128, ge=16, le=4096)
    microbatch: int = Field(default=64, ge=1, le=4096)
    gpu_layers: int = Field(default=99, ge=1, le=999)
    trials: int = Field(default=3, ge=1, le=10)
    warmup: int = Field(default=1, ge=0, le=2)
    trial_timeout_seconds: int = Field(default=60, ge=1, le=600)
    job_timeout_seconds: int = Field(default=600, ge=1, le=3600)

    @model_validator(mode="after")
    def bounds(self):
        if Path(self.binary.path).name != "llama-bench":
            raise ValueError("Only the reviewed llama-bench executable is supported")
        if self.microbatch > self.batch or self.trial_timeout_seconds > self.job_timeout_seconds:
            raise ValueError("Inconsistent workload bounds")
        return self

    def fingerprint(self):
        return hashlib.sha256(encode(self.model_dump()).encode()).hexdigest()

    def public(self):
        # Export identifiers/settings, never native paths or unredacted output.
        return {"preset_id": self.id, "engine": self.engine, "engine_revision": self.engine_revision,
                "protocol": self.protocol, "model_id": self.model_id, "model_license": self.model_license,
                "binary_sha256": self.binary.sha256, "model_sha256": self.model.sha256,
                "library_sha256": [a.sha256 for a in self.libraries],
                "declared_cuda_version": self.declared_cuda_version,
                "gpu_uuid": self.gpu_uuid, "required_driver_version": self.driver_version,
                "required_compute_capability": self.compute_capability, "preset_sha256": self.fingerprint(),
                "workload": {k: getattr(self, k) for k in (
                    "prompt_tokens", "output_tokens", "batch", "microbatch", "gpu_layers", "threads",
                    "trials", "warmup", "trial_timeout_seconds", "job_timeout_seconds")},
                "process_per_trial": True, "prompt_kind": "llama-bench generated token microbenchmark",
                "ttft_available": False, "harness_version": "0.3.0"}

    def argv(self):
        return [self.binary.path, "-m", self.model.path, "-p", str(self.prompt_tokens),
                "-n", str(self.output_tokens), "-r", "1", "-o", "json", "-t", str(self.threads),
                "-b", str(self.batch), "-ub", str(self.microbatch), "-ngl", str(self.gpu_layers)]

    def environment(self):
        directories = sorted({str(Path(a.path).parent) for a in [self.binary, *self.libraries]})
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "CUDA_VISIBLE_DEVICES": self.gpu_uuid, "OMP_NUM_THREADS": str(self.threads),
                "LD_LIBRARY_PATH": ":".join(directories)}


class Registry(Strict):
    version: Literal[1] = 1
    enabled: bool = False
    presets: list[NativePreset] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({p.id for p in self.presets}) != len(self.presets):
            raise ValueError("Duplicate preset ID")
        return self

    def approved(self, preset_id):
        for preset in self.presets:
            if preset.id == preset_id:
                if not self.enabled or not preset.reviewed:
                    raise NativeError("preset_not_approved")
                return preset
        raise NativeError("unknown_preset")


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NativeError("duplicate_json_key")
        result[key] = value
    return result


def load_registry(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 131072:
        raise NativeError("unsafe_registry")
    if os.name != "nt" and (info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022):
        raise NativeError("unsafe_registry_permissions")
    if path.parent.stat().st_mode & 0o022:
        raise NativeError("unsafe_registry_directory")
    return Registry.model_validate(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys))


def verify_artifacts(preset, check):
    identities = {}
    for kind, artifact in [("binary", preset.binary), ("model", preset.model),
                           *[("library", a) for a in preset.libraries]]:
        check()
        path = Path(artifact.path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= (2**40 if kind == "model" else 2**31):
            raise NativeError("unsafe_artifact")
        if info.st_mode & 0o022:
            raise NativeError("writable_artifact")
        if kind != "model" and hasattr(os, "getuid") and info.st_uid not in {0, os.getuid()}:
            raise NativeError("untrusted_artifact_owner")
        if kind == "binary" and not os.access(path, os.X_OK):
            raise NativeError("binary_not_executable")
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise NativeError("artifact_changed")
            magic = stream.read(4)
            if magic != (b"GGUF" if kind == "model" else b"\x7fELF"):
                raise NativeError("artifact_format")
            digest.update(magic)
            while block := stream.read(1024 * 1024):
                check()
                digest.update(block)
            after = os.fstat(stream.fileno())
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise NativeError("artifact_changed")
        if digest.hexdigest() != artifact.sha256:
            raise NativeError("artifact_hash_mismatch")
        identities[str(path)] = identity
    return identities


def unchanged(identities):
    for path, expected in identities.items():
        info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected:
            raise NativeError("artifact_changed")
