"""Host-only catalog and atomic configuration for the pinned Kmic unit."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .profile_settings import LIMITS, validate_settings

ID = r"[a-z0-9][a-z0-9._-]{0,63}"
SAFE_PATH = r"/[A-Za-z0-9/._+-]+"
STATE_FILE = Path("/var/lib/p100-lab-engine-agent/selection.env")
CATALOG_LIMIT = 24


def path_value(value):
    if not isinstance(value, str) or len(value) > 512 or not re.fullmatch(SAFE_PATH, value):
        raise ValueError("Unsafe host path")
    path = Path(value)
    if str(path) != value or ".." in path.parts:
        raise ValueError("Expected normalized host path")
    return path


def label(value, length):
    if not isinstance(value, str) or not 1 <= len(value) <= length or not value.isprintable():
        raise ValueError("Invalid catalog label")
    return value


class ManagedModels:
    def __init__(self, config, state_path=STATE_FILE):
        if not isinstance(config, dict) or set(config) != {"engine_version", "gpu", "models"}:
            raise ValueError("Invalid managed model configuration")
        self.engine_version = label(config["engine_version"], 80)
        self.gpu = label(config["gpu"], 80)
        self.path = state_path
        self.entries = {}
        models = config["models"]
        if not isinstance(models, list) or not 1 <= len(models) <= CATALOG_LIMIT:
            raise ValueError("Invalid catalog size")
        fields = {"id", "name", "quantization", "bytes", "sha256", "path", "alias", "limits", "initial"}
        for entry in models:
            if not isinstance(entry, dict) or set(entry) != fields:
                raise ValueError("Invalid model entry")
            model_id = label(entry["id"], 64)
            alias = label(entry["alias"], 64)
            if (not re.fullmatch(ID, model_id) or model_id in self.entries or not re.fullmatch(ID, alias)
                    or any(model["alias"] == alias for model in self.entries.values())):
                raise ValueError("Invalid or duplicate catalog identity")
            label(entry["name"], 100)
            label(entry["quantization"], 32)
            path = path_value(entry["path"])
            if path.suffix.lower() != ".gguf" or re.search(r"-\d{5}-of-\d{5}\.gguf$", path.name, re.I):
                raise ValueError("Only single-file GGUF models are supported")
            if type(entry["bytes"]) is not int or not 1 <= entry["bytes"] <= 2**53 - 1:
                raise ValueError("Expected exact file size")
            if not re.fullmatch(r"[0-9a-f]{64}", label(entry["sha256"], 64)):
                raise ValueError("Expected verified artifact SHA-256")
            limits = validate_settings(entry["limits"])
            validate_settings(entry["initial"], limits["context"], limits["batch"], limits["ubatch"])
            self.entries[model_id] = {**entry, "path": path}

    def available(self, entry):
        path = entry["path"]
        try:
            info = path.lstat()
            return (stat.S_ISREG(info.st_mode) and info.st_size == entry["bytes"]
                    and os.access(path, os.R_OK) and path.resolve(strict=True) == path)
        except (OSError, RuntimeError):
            return False

    def public(self):
        excluded = {"path", "alias"}
        return [
            {**{key: value for key, value in entry.items() if key not in excluded},
             "available": self.available(entry), "engines": ["kmic"], "engine_version": self.engine_version,
             "gpu": self.gpu, "gpu_count": 2}
            for entry in self.entries.values()
        ]

    def selection(self, model_id, settings, require_available=False):
        if not isinstance(model_id, str) or model_id not in self.entries:
            raise ValueError("Model is not approved for Kmic")
        entry = self.entries[model_id]
        limits = entry["limits"]
        validate_settings(settings, limits["context"], limits["batch"], limits["ubatch"])
        if require_available and not self.available(entry):
            raise ValueError("Approved model file is unavailable")
        return entry

    def render(self, model_id, settings):
        entry = self.selection(model_id, settings)
        data = json.dumps({"model_id": model_id, "settings": settings}, sort_keys=True, separators=(",", ":"))
        lines = [f"# {data}", f"LAB_MODEL={entry['path']}", f"LAB_ALIAS={entry['alias']}"]
        lines.extend(f"LAB_{field.upper()}={settings[field]}" for field in LIMITS)
        return "\n".join(lines) + "\n"

    def read(self):
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Expected a private service-owned selection file")
        with self.path.open(encoding="ascii") as stream:
            raw = stream.read(2049)
        if not raw.startswith("# ") or "\n" not in raw or len(raw) > 2048:
            raise ValueError("Invalid configured selection")
        selection = json.loads(raw[2:].split("\n", 1)[0])
        if not isinstance(selection, dict) or set(selection) != {"model_id", "settings"}:
            raise ValueError("Invalid configured selection")
        if raw != self.render(selection["model_id"], selection["settings"]):
            raise ValueError("Configured selection changed outside the agent")
        return selection

    def stage(self, model_id, settings):
        self.selection(model_id, settings, require_available=True)
        data = self.render(model_id, settings)
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=self.path.parent, delete=False) as stream:
            temp = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {"model_id": model_id, "settings": settings}
