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
    worker = name + "-worker"
    image = os.getenv("LAB_TEST_IMAGE", "p100-lab:phase1")
    token = secrets.token_urlsafe(48)
    job_token = secrets.token_urlsafe(48)
    env = {**os.environ, "LAB_API_TOKEN": token, "LAB_JOB_TOKEN": job_token}

    def docker(*args):
        return subprocess.check_output(["docker", *args], env=env, text=True).strip()

    def start(fixtures=False):
        extra = ["-e", "LAB_ENABLE_FIXTURE_JOBS=true", "-e", "LAB_JOB_TOKEN"] if fixtures else []
        docker("run", "-d", "--name", name, "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges:true", "--tmpfs", "/tmp",
               "-p", "127.0.0.1::8000", "-e", "LAB_API_TOKEN", "-e", "LAB_TELEMETRY=mock",
               "-v", volume + ":/data", *extra, image)
        port = docker("port", name, "8000/tcp").rsplit(":", 1)[1]
        url = "http://127.0.0.1:" + port
        for _ in range(80):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=2).close()
                return url
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        raise RuntimeError("Container did not become ready")

    def request(path, body=None, authenticated=True, privileged=False, key=None):
        headers = {"Authorization": "Bearer " + token} if authenticated else {}
        if privileged:
            headers["X-Lab-Job-Token"] = job_token
        if key:
            headers["Idempotency-Key"] = key
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

    def submit(body):
        status, _, content = request("/api/benchmark-jobs", body, privileged=True, key=str(uuid.uuid4()))
        assert status == 201
        return json.loads(content)["job"]

    def wait_for_job(job_id, expected):
        for _ in range(100):
            status, _, content = request("/api/benchmark-jobs/" + job_id)
            job = json.loads(content)
            if status == 200 and job["status"] == expected:
                return job
            time.sleep(0.1)
        raise AssertionError("Fixture job did not reach " + expected)

    try:
        url = start()
        status, headers, body = request("/", authenticated=False)
        assert status == 200 and b"<html" in body
        assert headers["X-Frame-Options"] == "DENY"
        for path in ("system", "engines", "models", "benchmarks", "benchmark-presets", "benchmark-jobs", "native-benchmarks"):
            assert request("/api/" + path, authenticated=False)[0] == 401
        assert json.loads(request("/api/system")[2])["mode"] == "mock"
        assert request("/api/engines/kmic/control", {"action": "start"})[0] == 403
        assert json.loads(request("/api/native-benchmarks")[2])["configured"] is False
        assert request("/api/native-benchmarks", {"preset": "anything"})[0] == 403
        assert request("/api/benchmarks", {"engine": "kmic", "model": "smoke.gguf", "tokens_per_second": 1})[0] == 201
        docker("rm", "-f", name)
        url = start()
        rows = json.loads(request("/api/benchmarks")[2])["items"]
        assert len(rows) == 1 and rows[0]["source"] == "manual"
        assert docker("exec", name, "id", "-u") == "10001"
        assert request("/api/benchmark-jobs", {}, privileged=True, key=str(uuid.uuid4()))[0] == 403
        docker("rm", "-f", name)
        url = start(fixtures=True)
        assert request("/api/benchmark-jobs", {}, key=str(uuid.uuid4()))[0] == 403
        cancelled = submit({})
        assert json.loads(request("/api/benchmark-jobs/" + cancelled["id"] + "/cancel", {}, privileged=True)[2])["status"] == "cancelled"
        queued = submit({"trials": 3, "warmup": 2})
        worker_args = ["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                       "--no-healthcheck", "--network", "none", "--tmpfs", "/tmp", "-e", "LAB_ENABLE_FIXTURE_JOBS=true",
                       "-v", volume + ":/data", "--entrypoint", "python", image, "-m", "lab.worker"]
        docker("run", "-d", "--name", worker, *worker_args)
        complete = wait_for_job(queued["id"], "succeeded")
        assert complete["source"] == "fixture" and len(complete["samples"]) == 5
        assert complete["summary"]["sample_count"] == 3
        assert docker("exec", worker, "id", "-u") == "10001"
        assert docker("inspect", "--format", "{{.HostConfig.NetworkMode}}", worker) == "none"
        worker_environment = json.loads(docker("inspect", "--format", "{{json .Config.Env}}", worker))
        assert not any(e.startswith(("LAB_API_TOKEN", "LAB_JOB_TOKEN")) for e in worker_environment)
        contender = subprocess.run(["docker", "run", "--rm", *worker_args], env=env, capture_output=True, text=True)
        assert contender.returncode != 0 and "Another fixture worker" in contender.stderr
        failed = submit({"preset": "fixture-failure-v1"})
        assert wait_for_job(failed["id"], "failed")["error_code"] == "fixture_failure"
        timed_out = submit({"trials": 20, "warmup": 0, "timeout_seconds": 0.1})
        wait_for_job(timed_out["id"], "timed_out")
        assert len(json.loads(request("/api/benchmarks")[2])["items"]) == 1
        docker("stop", "--time", "5", worker)
        docker("rm", "-f", name)
        url = start(fixtures=True)
        assert json.loads(request("/api/benchmark-jobs/" + queued["id"])[2])["summary"] == complete["summary"]
        print("Container smoke passed: authenticated API, persistent manual history, separately authorized fixture queue, non-root single worker, warmup/trial summaries, failure/timeout/cancellation and durable results. No native engine launched.")
    finally:
        subprocess.run(["docker", "rm", "-f", worker], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
