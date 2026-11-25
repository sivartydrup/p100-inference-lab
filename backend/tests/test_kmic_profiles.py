import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lab.kmic_profiles import FAST, REASONING, FULL_CONTEXT, FLASH, FLASH_SETTINGS, launch_arguments, public_recipe
from lab.managed_models import ManagedModels, bundle_digest

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "deploy/engine-agent/flash-next.profiles.json"
linux = pytest.mark.skipif(os.name != "posix", reason="Host paths and private state are Ubuntu-only")


@pytest.mark.parametrize("runtime,thinking,budget,temp,context,cache", [
    (FAST, False, "0", "0.7", "32768", "20480"),
    (REASONING, True, "1024", "1.0", "32768", "20480"),
    (FULL_CONTEXT, True, "1024", "1.0", "262144", "16384"),
])
def test_reviewed_recipe_and_client_agree(runtime, thinking, budget, temp, context, cache):
    entry = {"path": "/fixed/main.gguf", "alias": "flash-next", "runtime": runtime,
             "draft": {"path": "/fixed/draft.gguf"}}
    argv = launch_arguments(entry, FLASH[runtime])
    def value(flag):
        assert argv.count(flag) == 1
        return argv[argv.index(flag) + 1]
    assert value("-sm") == "layer" and value("-ts") == "55,45"
    assert value("-c") == context
    assert value("--moe-cache-mib") == cache and value("--spec-draft-n-max") == "2"
    assert value("--spec-draft-device") == "CUDA1" and value("--load-mode") == "none"
    assert value("--reasoning-budget") == budget and value("--temp") == temp
    assert json.loads(value("--chat-template-kwargs"))["enable_thinking"] is thinking
    assert public_recipe(runtime)["client_settings"]["chat_template_kwargs"]["enable_thinking"] is thinking
    with pytest.raises(ValueError):
        launch_arguments(entry, {**FLASH_SETTINGS, "context": 16384})
    with pytest.raises(ValueError):
        launch_arguments({**entry, "runtime": "--shell"}, FLASH_SETTINGS)


@linux
def test_full_context_profile_stages_and_launches_at_native_capacity(tmp_path):
    models = ManagedModels(fixture_catalog(tmp_path), tmp_path / "selection.env")
    profile = next(model for model in models.public() if model["recipe"]["id"] == FULL_CONTEXT)
    settings = {**FLASH_SETTINGS, "context": 262144, "ubatch": 256}
    assert profile["initial"] == profile["limits"] == settings
    assert profile["recipe"]["client_settings"]["chat_template_kwargs"]["enable_thinking"]
    models.stage(profile["id"], settings)
    assert models.read() == {"model_id": profile["id"], "settings": settings}
    entry = models.selection(profile["id"], settings)
    argv = launch_arguments(entry, settings)
    assert argv[argv.index("-c") + 1] == "262144"
    assert argv[argv.index("-ub") + 1] == "256"
    assert argv[argv.index("--moe-cache-mib") + 1] == str(profile["recipe"]["expert_cache_mib"]) == "16384"
    with pytest.raises(ValueError):
        models.selection(profile["id"], FLASH_SETTINGS)
    with pytest.raises(ValueError):
        launch_arguments(entry, FLASH_SETTINGS)


def test_existing_27b_recipe_keeps_native_flags():
    args = launch_arguments({"path": "/existing.gguf", "alias": "original"},
                            {"context": 262144, "batch": 2048, "ubatch": 2048})
    for flag, value in [("-sm", "tensor"), ("-ctk", "q4_0"), ("--spec-draft-n-max", "4"), ("-c", "262144")]:
        assert args[args.index(flag) + 1] == value
    assert "-md" not in args and "--moe-cache-mib" not in args


def fixture_catalog(tmp_path):
    config = json.loads(CATALOG.read_text(encoding="utf-8"))
    for entry in config["models"]:
        files = []
        for original in entry["files"]:
            path = tmp_path / Path(original["path"]).name
            path.write_bytes(b"GGUF")
            files.append({**original, "path": str(path), "bytes": 4})
        draft = tmp_path / "draft.gguf"
        draft.write_bytes(b"head")
        entry.update(path=files[0]["path"], files=files, bytes=16, sha256=bundle_digest(files),
                     draft={**entry["draft"], "path": str(draft), "bytes": 4})
    return config


@linux
def test_all_artifacts_required_and_private_paths_not_exposed(tmp_path):
    config = fixture_catalog(tmp_path)
    models = ManagedModels(config, tmp_path / "selection.env")
    entry = models.public()[0]
    assert entry["available"] and entry["recipe"]["mode"] == "Fast"
    original_recipe = entry["recipe"]["sha256"]
    changed = fixture_catalog(tmp_path)
    changed["models"][0]["draft"]["sha256"] = "d" * 64
    assert ManagedModels(changed).public()[0]["recipe"]["sha256"] != original_recipe
    assert str(tmp_path) not in json.dumps(models.public())
    model_id = entry["id"]
    models.stage(model_id, FLASH_SETTINGS)
    assert models.read() == {"model_id": model_id, "settings": FLASH_SETTINGS}
    assert models.path.stat().st_mode & 0o777 == 0o600
    assert models.path.read_text().count("LAB_MODEL=") == 1
    for kind, index in [("files", 2), ("draft", None)]:
        item = config["models"][0][kind][index] if index is not None else config["models"][0][kind]
        path = Path(item["path"])
        path.unlink()
        assert not models.public()[0]["available"]
        with pytest.raises(ValueError):
            models.selection(model_id, FLASH_SETTINGS, require_available=True)
        path.write_bytes(b"four")
    shard = Path(config["models"][0]["files"][1]["path"])
    target = tmp_path / "elsewhere.gguf"
    target.write_bytes(b"four")
    shard.unlink()
    shard.symlink_to(target)
    assert not models.public()[0]["available"]


@linux
@pytest.mark.parametrize("change", ["order", "missing", "size", "digest", "draft", "flags", "settings", "path"])
def test_catalog_refuses_incomplete_or_unreviewed_profiles(tmp_path, change):
    config = fixture_catalog(tmp_path)
    entry = config["models"][0]
    if change == "order": entry["files"].reverse()
    if change == "missing": entry["files"].pop()
    if change == "size": entry["bytes"] += 1
    if change == "digest": entry["files"][1]["sha256"] = "b" * 64
    if change == "draft": entry.pop("draft")
    if change == "flags": entry["argv"] = ["--shell"]
    if change == "settings": entry["initial"]["context"] = 16384
    if change == "path": entry["files"][1]["path"] = "/tmp/../unapproved.gguf"
    with pytest.raises(ValueError):
        ManagedModels(config)


@linux
def test_actual_catalog_identity_and_recipe():
    catalog = ManagedModels(json.loads(CATALOG.read_text(encoding="utf-8")))
    for entry in catalog.entries.values():
        assert entry["bytes"] == 83626438426
        assert entry["draft"]["bytes"] == 2786204800
        assert len(entry["files"]) == 4
        assert launch_arguments(entry, entry["initial"])


@linux
def test_launcher_rechecks_binary_and_does_not_use_inherited_engine_flags(tmp_path):
    from lab.kmic_launcher import prepared_command
    from lab.kmic_profiles import FLASH_BINARY_SHA256
    config = {"managed_models": fixture_catalog(tmp_path)}
    models = ManagedModels(config["managed_models"], tmp_path / "selection.env")
    models.stage(next(iter(models.entries)), FLASH_SETTINGS)
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"fixture")
    binary.chmod(0o700)
    with patch("lab.kmic_launcher.ManagedModels", return_value=models):
        with pytest.raises(ValueError, match="benchmarked"):
            prepared_command(config, binary, "127.0.0.1", 18081, tmp_path / "key")
        with patch("lab.kmic_launcher.hashlib.sha256") as digest, patch.dict(os.environ, {"LLAMA_ARG_MODEL": "/evil", "GGML_CUDA_P2P": "0"}):
            digest.return_value.hexdigest.return_value = FLASH_BINARY_SHA256
            argv, env = prepared_command(config, binary, "127.0.0.1", 18081, tmp_path / "key")
            assert argv[0] == str(binary) and "--api-key-file" in argv
            assert "LLAMA_ARG_MODEL" not in env and env["GGML_CUDA_P2P"] == "1"
            assert "LLAMA_SPEC_SAMPLE_TEMP" not in env
            Path(config["managed_models"]["models"][0]["draft"]["path"]).unlink()
            with pytest.raises(ValueError):
                prepared_command(config, binary, "127.0.0.1", 18081, tmp_path / "key")
