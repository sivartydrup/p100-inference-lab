"""Root controller trust/recording checks in a disposable image; no GPU or host bus."""
import os
import subprocess
from uuid import uuid4

IMAGE = os.environ.get('LAB_TEST_IMAGE', 'p100-lab:phase1')
name = 'p100-admin-check-' + uuid4().hex[:10]
code = r'''
import json,os,subprocess,sys,threading,io
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4
from lab.admin_cli import main
from lab import admin_security as security,native_store as store
from lab.native_process import Captured
from lab.native_system import AdminSystemdSupervisor
from lab.native_config import NativePreset,Registry,NativeError
from lab.native_runner import execute,recover
assert os.geteuid()==0 and sys.flags.isolated
root=Path('/var/lib/admin-smoke');root.mkdir(mode=0o700)
db=root/'native.sqlite3'
main(['--db',str(db),'init'])
assert db.stat().st_mode & 0o077 == 0
assert security.identity(db)['payload_uid'] is None
try: main(['--db',str(db),'init'])
except SystemExit: pass
else: raise AssertionError('Existing admin database was accepted as new')
security.identity(db,10001,10001)
try: security.identity(db,1000,1000)
except security.AdminTrustError: pass
else: raise AssertionError('Payload account changed')
try: security.database(Path('/tmp/untrusted.sqlite3'),missing=True)
except security.AdminTrustError: pass
else: raise AssertionError('Writable parent accepted')
supervisor=object.__new__(AdminSystemdSupervisor);supervisor.group_id=10001
with supervisor.gate_context() as directory:
 p=Path(directory)/'approved'
 assert Path(directory).stat().st_uid==0 and Path(directory).stat().st_mode & 0o777==0o710
 before=subprocess.run(['/usr/sbin/runuser','-u','lab','--',sys.executable,'-I','-c','from pathlib import Path;Path('+repr(str(p))+').write_bytes(bytes.fromhex("617070726f7665640a"))'],capture_output=True)
 assert before.returncode!=0 and not p.exists()
 supervisor.approve_gate(p)
 assert p.stat().st_uid==0 and p.stat().st_mode & 0o777==0o640
 after=subprocess.run(['/usr/sbin/runuser','-u','lab','--',sys.executable,'-I','-c','from pathlib import Path;assert Path('+repr(str(p))+').read_bytes()==bytes.fromhex("617070726f7665640a")'],capture_output=True)
 assert after.returncode==0,after.stderr
assert not Path(directory).exists()
# Real SQLite transactions and recovery; synthetic protocol/hardware, no unit launched.
binary=root/'llama-bench';binary.write_bytes(b'\x7fELFsynthetic')
model=root/'model.gguf';model.write_bytes(b'GGUFsynthetic')
library=root/'library.so';library.write_bytes(b'\x7fELFsynthetic')
data={'id':'synthetic-admin','reviewed':True,'engine':'kmic','engine_revision':'a'*40,
 'binary':{'path':str(binary),'sha256':'b'*64},'libraries':[{'path':str(library),'sha256':'c'*64}],
 'model_id':'synthetic','model':{'path':str(model),'sha256':'d'*64},'model_license':'Synthetic test only',
 'declared_cuda_version':'synthetic','gpu_uuid':'GPU-11111111-1111-1111-1111-111111111111',
 'compute_capability':'6.0','driver_version':'123.45','min_free_vram_mib':256,'min_free_ram_mib':256,
 'max_temperature_c':75,'memory_max_mib':256,'threads':1,'trials':2,'warmup':1}
preset=NativePreset.model_validate(data)
registry=root/'registry.json'
request=str(uuid4())
submit=['--db',str(db),'submit','--registry',str(registry),'--preset',preset.id,'--request-id',request]
os.environ.pop('LAB_ENABLE_ADMIN_RUNNER',None)
registry.write_text(json.dumps({'enabled':True,'presets':[data]}))
try:main(submit)
except SystemExit:pass
else:raise AssertionError('Administrator opt-in was bypassed')
assert store.list_jobs(db)==[]
os.environ['LAB_ENABLE_ADMIN_RUNNER']='true'
for document in ({'enabled':False,'presets':[data]}, {'enabled':True,'presets':[{**data,'reviewed':False}]}):
 registry.write_text(json.dumps(document))
 try:main(submit)
 except SystemExit:pass
 else:raise AssertionError('Registry/preset gate was bypassed')
 assert store.list_jobs(db)==[]
registry.write_text(json.dumps({'enabled':True,'presets':[data]}))
with redirect_stdout(io.StringIO()):main(submit);main(submit)
assert len(store.list_jobs(db))==1
queued=store.list_jobs(db)[0]
main(['--db',str(db),'cancel',queued['id']])
assert store.get_job(db,queued['id'])['status']=='cancelled'
job=store.enqueue(db,preset,str(uuid4()),transport='admin-systemd-v1');store.claim(db)
from lab.native_process import unit_name
unit=unit_name(job['id'],'measured',0);store.plan(db,job['id'],'measured',0,unit)
class Cleanup:
 def available(self):pass
 def listed_units(self):return {unit}
 def stop(self,value):assert value==unit
recover(db,Cleanup())
assert store.get_job(db,job['id'])['status']=='interrupted' and store.claim(db) is None
assert store.get_job(db,job['id'])['provenance']['execution_transport']=='admin-systemd-v1'
assert store.get_job(db,job['id'])['provenance']['execution_policy']=={'power_limit_w':150,'temperature_cutoff_c':75}
print('Administrator smoke passed: sealed root runtime/database, immutable payload binding, non-forgeable group-readable gate and durable intent/recovery without replay. No GPU/systemd bus/host mount.')
'''
subprocess.run(['docker', 'run', '--rm', '--name', name, '--user', '0:0', '--network', 'none', '--read-only',
                '--tmpfs', '/var/lib', '--tmpfs', '/run', '--tmpfs', '/tmp', '--entrypoint', 'python',
                IMAGE, '-I', '-c', code], check=True)
