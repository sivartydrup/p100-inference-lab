"""Local container acceptance test; never connects to the Ubuntu server."""
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def main():
    name = "p100-smoke-" + uuid.uuid4().hex[:10]
    volume = name + "-data"
    token = secrets.token_urlsafe(48)
    env = {**os.environ, "LAB_API_TOKEN": token}

    def docker(*args):
        return subprocess.check_output(["docker", *args], env=env, text=True).strip()

    def start():
        docker("run", "-d", "--name", name, "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges:true", "--tmpfs", "/tmp",
               "-p", "127.0.0.1::8000", "-e", "LAB_API_TOKEN", "-e", "LAB_TELEMETRY=mock",
               "-v", volume + ":/data", "p100-lab:phase1")
        port = docker("port", name, "8000/tcp").rsplit(":", 1)[1]
        url = "http://127.0.0.1:" + port
        for _ in range(80):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=2).close()
                return url
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        raise RuntimeError("Container did not become ready")

    def request(path, body=None, authenticated=True):
        headers = {"Authorization": "Bearer " + token} if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, response.headers, content

    try:
        url = start()
        status, headers, body = request("/", authenticated=False)
        assert status == 200 and b"<html" in body
        assert headers["X-Frame-Options"] == "DENY"
        for path in ("system", "engines", "models", "benchmarks"):
            assert request("/api/" + path, authenticated=False)[0] == 401
        assert json.loads(request("/api/system")[2])["mode"] == "mock"
        assert request("/api/engines/kmic/control", {"action": "start"})[0] == 403
        assert request("/api/benchmarks", {"engine": "kmic", "model": "smoke.gguf", "tokens_per_second": 1})[0] == 201
        docker("rm", "-f", name)
        url = start()
        rows = json.loads(request("/api/benchmarks")[2])["items"]
        assert len(rows) == 1 and rows[0]["source"] == "manual"
        assert docker("exec", name, "id", "-u") == "10001"
        print("Container smoke passed: static UI, authentication, mock telemetry, disabled controls, non-root runtime and persistent SQLite.")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
