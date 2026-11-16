"""Real subprocess fixtures on Linux; no systemd units, native engines or GPUs."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from lab import native_store as store
from lab.native_config import NativeError, NativePreset, Registry, load_registry, verify_artifacts, unchanged
from lab.native_process import capture, Captured
from lab.native_runner import execute
from test_native import preset_data, Hardware

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Native process boundary is Ubuntu/Linux-only")


@pytest.fixture
def artifacts(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler required for the isolated ELF benchmark fixture")
    code = tmp_path / "bench.c"
    code.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char *arg(int argc,char **argv,char *key) {
 for(int i=1;i<argc-1;i++)if(!strcmp(argv[i],key))return argv[i+1];
 return "0";
}
int main(int argc,char **argv) {
 if(getenv("PRIVATE_TEST_SECRET"))return 17;
 char *model=arg(argc,argv,"-m");
 int p=atoi(arg(argc,argv,"-p")),n=atoi(arg(argc,argv,"-n"));
 int t=atoi(arg(argc,argv,"-t")),b=atoi(arg(argc,argv,"-b")),ub=atoi(arg(argc,argv,"-ub")),ngl=atoi(arg(argc,argv,"-ngl"));
 printf("[");
 for(int i=0;i<2;i++) {
  int pp=i?0:p,gg=i?n:0; long long ns=i?2000000000LL:1000000000LL;
  double rate=(pp+gg)*1000000000.0/ns;
  printf("%s{\"build_commit\":\"aaaaaaaaa\",\"backends\":\"CUDA\",\"model_filename\":\"%s\",\"n_prompt\":%d,\"n_gen\":%d,\"n_threads\":%d,\"n_batch\":%d,\"n_ubatch\":%d,\"n_gpu_layers\":%d,\"samples_ns\":[%lld],\"samples_ts\":[%f],\"avg_ns\":%lld,\"avg_ts\":%f}",i?",":"",model,pp,gg,t,b,ub,ngl,ns,rate,ns,rate);
 }
 printf("]\n");return 0;
}
''')
    binary = tmp_path / "llama-bench"
    subprocess.run([compiler, str(code), "-o", str(binary)], check=True, capture_output=True)
    binary.chmod(0o700)
    model = tmp_path / "fixture.gguf"
    model.write_bytes(b"GGUFisolated-test-marker-not-model-weights")
    library = tmp_path / "libfixture.so"
    shutil.copyfile(binary, library)
    library.chmod(0o600)
    data = preset_data()
    for key, path in (("binary",binary),("model",model)):
        data[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    data["libraries"] = [{"path":str(library),"sha256":hashlib.sha256(library.read_bytes()).hexdigest()}]
    return NativePreset.model_validate(data)


class LocalFixtureSupervisor:
    """Test-only transport. Production CLI cannot select this implementation."""
    def __init__(self):
        self.cleaned = []

    def run(self, preset, unit, deadline, check):
        return capture(preset.argv(), preset.environment(), deadline, check)

    def stop(self, unit):
        self.cleaned.append(unit)


def test_real_elf_fixture_uses_same_hash_protocol_and_result_boundary(tmp_path, artifacts):
    db = tmp_path / "native.sqlite3"
    store.initialize(db)
    queued = store.enqueue(db, artifacts, str(uuid4()))
    supervisor = LocalFixtureSupervisor()
    execute(db, store.claim(db), Registry(enabled=True,presets=[artifacts]), supervisor, Hardware(), threading.Event())
    result = store.get_job(db, queued["id"])
    assert result["status"] == "succeeded" and result["summary"]["sample_count"] == 2
    assert len(supervisor.cleaned) == 3
    assert result["attempts"][0]["sample"]["process_wall_ms"] > 0


def test_hash_and_file_identity_changes_prevent_native_execution(tmp_path, artifacts):
    identities = verify_artifacts(artifacts, lambda: None)
    Path(artifacts.model.path).write_bytes(b"GGUFmodified")
    with pytest.raises(NativeError, match="artifact_changed"):
        unchanged(identities)
    with pytest.raises(NativeError, match="artifact_hash_mismatch"):
        verify_artifacts(artifacts, lambda: None)
    Path(artifacts.binary.path).chmod(0o777)
    with pytest.raises(NativeError, match="writable_artifact"):
        verify_artifacts(artifacts, lambda: None)


def test_registry_duplicate_keys_permissions_and_unreviewed_defaults(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"enabled":false,"enabled":true}')
    path.chmod(0o600)
    with pytest.raises(NativeError, match="duplicate_json_key"):
        load_registry(path)
    path.write_text('{"enabled":false,"presets":[]}')
    assert not load_registry(path).enabled
    path.chmod(0o666)
    with pytest.raises(NativeError, match="unsafe_registry_permissions"):
        load_registry(path)


def test_output_and_deadline_limits_use_actual_subprocesses():
    with pytest.raises(NativeError, match="output_limit_exceeded"):
        capture([sys.executable,"-c","import os; os.write(1,b'x'*1000000)"], {}, time.monotonic()+5, limit=1024)
    with pytest.raises(NativeError, match="deadline_exceeded"):
        capture([sys.executable,"-c","import time; time.sleep(30)"], {}, time.monotonic()+0.2)


def test_cancellation_kills_process_group_with_descendant(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = "import os,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=os.fork();\nif p==0: time.sleep(30)\nelse:\n open(%r,'w').write(str(p)); time.sleep(30)" % str(pidfile)
    def cancel():
        if pidfile.exists():
            raise NativeError("cancelled")
    with pytest.raises(NativeError, match="cancelled"):
        capture([sys.executable,"-c",code],{},time.monotonic()+5,cancel)
    pid = int(pidfile.read_text())
    for _ in range(100):
        info = Path(f"/proc/{pid}/stat")
        if not info.exists() or info.read_text().split()[2] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("Fixture descendant survived process-group cancellation")


def test_gate_does_not_execute_without_approval_and_clears_environment(tmp_path, artifacts):
    child = Path(__file__).parents[1] / "lab" / "native_child.py"
    gate = tmp_path / "gate"
    # A dead worker PID fails before an executable can start, even with an approval file.
    gate.write_bytes(b"approved\n")
    cmd=[sys.executable,str(child),"--gate",str(gate),"--worker-pid","2147483647",
         "--environment",json.dumps(artifacts.environment()),"--",*artifacts.argv()]
    result=subprocess.run(cmd,capture_output=True,timeout=5)
    assert result.returncode != 0 and not result.stdout
    # A live worker and valid gate can exec the bounded test binary; production only creates
    # this file after systemd policy attestation (covered separately).
    cmd[cmd.index("2147483647")] = str(os.getpid())
    gate.unlink()
    process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={"PRIVATE_TEST_SECRET":"must-not-inherit"})
    try:
        time.sleep(0.1)
        assert process.poll() is None
        gate.write_bytes(b"approved\n")
        stdout,stderr=process.communicate(timeout=5)
        assert process.returncode == 0 and b'"samples_ns"' in stdout
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=5)
