"""Offline import + read-only snapshot mounted in the real unprivileged API image."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from resident_fixture import measurement

IMAGE = os.environ.get("LAB_TEST_IMAGE", "p100-lab:phase1")
prefix = "p100-history-test-" + uuid4().hex[:10]
api, history, web = prefix + "-api", prefix + "-history", prefix + "-web"
token = secrets.token_urlsafe(40)


def docker(*args, input=None, check=True):
    return subprocess.run(["docker", *args], input=input, text=True, capture_output=True, check=check)


try:
    docker("volume", "create", history)
    docker("volume", "create", web)
    # No API token, GPU, network or host filesystem is given to this one-off importer.
    setup = """import json,sys,tempfile
from pathlib import Path
from lab import native_store
from lab.measured_history import MeasuredRun,import_run,publish_snapshot
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'private.sqlite3'; native_store.initialize(p)
 run=MeasuredRun.model_validate_json(sys.stdin.read())
 import_run(p,run); import_run(p,run)
 publish_snapshot(p,Path('/data'))
"""
    docker("run", "--rm", "-i", "--network", "none", "--mount", f"type=volume,src={history},dst=/data",
           "--entrypoint", "python", IMAGE, "-c", setup, input=json.dumps(measurement()))
    docker("run", "-d", "--name", api, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--tmpfs", "/tmp:size=16m", "--mount", f"type=volume,src={web},dst=/data",
           "--mount", f"type=volume,src={history},dst=/native-history,readonly",
           "-e", "LAB_API_TOKEN=" + token, "-e", "LAB_TELEMETRY=mock", "-e", "LAB_NATIVE_DB=/native-history/native.sqlite3",
           "-p", "127.0.0.1::8000", IMAGE)
    inspect = json.loads(docker("inspect", api).stdout)[0]
    assert inspect["Config"]["User"] == "10001:10001"
    assert not next(m for m in inspect["Mounts"] if m["Destination"] == "/native-history")["RW"]
    port = inspect["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
    base = "http://127.0.0.1:" + port

    def request(path, auth=True, method="GET"):
        headers = {"Authorization": "Bearer " + token} if auth else {}
        req = urllib.request.Request(base + path, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as result:
                return result.status, json.loads(result.read())
        except urllib.error.HTTPError as error:
            return error.code, None

    deadline = time.monotonic() + 30
    while True:
        try:
            if request("/healthz", auth=False)[0] == 200:
                break
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError("API startup deadline")
        time.sleep(.25)
    assert request("/api/native-benchmarks", auth=False)[0] == 401
    code, listed = request("/api/native-benchmarks")
    assert code == 200 and listed["execution_available_via_api"] is False and len(listed["items"]) == 1
    job = listed["items"][0]
    assert job["provenance"]["record_origin"] == "operator_import"
    code, exported = request("/api/native-benchmarks/" + job["id"] + "/export")
    assert code == 200 and len(exported["attempts"]) == 5
    assert exported["summary"]["metrics"]["decode_tokens_per_second"]["median"] == 191.25
    assert request("/api/native-benchmarks", method="POST")[0] == 403
    assert request("/api/benchmarks")[1] == {"items": []}
    before = docker("exec", api, "python", "-c", "import hashlib; print(hashlib.sha256(open('/native-history/native.sqlite3','rb').read()).hexdigest())").stdout
    attempt = docker("exec", api, "python", "-c", "open('/native-history/native.sqlite3','wb').write(b'changed')", check=False)
    assert attempt.returncode != 0
    after = docker("exec", api, "python", "-c", "import hashlib; print(hashlib.sha256(open('/native-history/native.sqlite3','rb').read()).hexdigest())").stdout
    assert before == after
    assert request("/api/native-benchmarks")[0] == 200
    print("Native history smoke passed: offline import/idempotency, read-only snapshot, non-root API, authenticated export, no HTTP execution or database writes.")
finally:
    docker("rm", "-f", api, check=False)
    docker("volume", "rm", history, check=False)
    docker("volume", "rm", web, check=False)
