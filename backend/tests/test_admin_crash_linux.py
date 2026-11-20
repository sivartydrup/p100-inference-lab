"""Real worker SIGKILL and SQLite recovery; synthetic transport/hardware, no GPU/systemd."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from uuid import uuid4

import pytest

from lab import native_store as store
from lab.native_config import NativePreset
from lab.native_runner import recover
from lab.worker import worker_lock
from test_native import preset_data

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Real SIGKILL/pipe acceptance is Linux-only')

CHILD = r'''
import json,sys,threading,time
from pathlib import Path
from lab import native_runner as runner
from lab.native_config import NativePreset,Registry
from lab.native_process import Captured
preset=NativePreset.model_validate_json(sys.argv[2])
runner.verify_artifacts=lambda *args:{}
runner.unchanged=lambda *args:None
class Hardware:
 def preflight(self,*args):return {'synthetic':True}
 def snapshot(self,*args):return {'synthetic':True}
class Transport:
 def available(self):pass
 def listed_units(self):return set()
 def stop(self,unit):pass
 def run(self,preset,unit,deadline,check):
  if '-measured-' in unit:
   print(json.dumps({'waiting_unit':unit}),flush=True)
   time.sleep(60)
  rows=[]
  for prompt,generated in [(preset.prompt_tokens,0),(0,preset.output_tokens)]:
   rate=prompt+generated
   rows.append({'n_prompt':prompt,'n_gen':generated,'n_threads':preset.threads,'n_batch':preset.batch,
    'n_ubatch':preset.microbatch,'n_gpu_layers':preset.gpu_layers,'model_filename':preset.model.path,
    'build_commit':preset.engine_revision[:9],'backends':'CUDA synthetic fixture',
    'samples_ns':[1000000000],'samples_ts':[rate],'avg_ns':1000000000,'avg_ts':rate})
  return Captured(0,json.dumps(rows).encode(),b'',12.5)
runner.serve(Path(sys.argv[1]),Registry(enabled=True,presets=[preset]),Transport(),Hardware(),threading.Event(),once=True,lock_path=Path(sys.argv[3]))
'''


def test_worker_crash_preserves_samples_intent_and_releases_locks_without_replay(tmp_path):
    db=tmp_path/'native.sqlite3';store.initialize(db)
    preset=NativePreset.model_validate({**preset_data(),'max_temperature_c':75})
    job=store.enqueue(db,preset,str(uuid4()),transport='admin-systemd-v1')
    lock=tmp_path/'global'
    process=subprocess.Popen([sys.executable,'-I','-c',CHILD,str(db),preset.model_dump_json(),str(lock)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    try:
        assert selector.select(10),'Worker did not reach measured trial'
        unit=json.loads(process.stdout.readline())['waiting_unit']
        process.kill();process.wait(timeout=5)
        before=store.get_job(db,job['id'])
        assert before['status']=='running' and len(before['attempts'])==2
        assert next(a for a in before['attempts'] if a['phase']=='warmup')['sample']['process_wall_ms']==12.5
        assert next(a for a in before['attempts'] if a['phase']=='measured')['sample'] is None
        class Recovery:
            def __init__(self):self.units={unit};self.stopped=[]
            def available(self):pass
            def listed_units(self):return self.units.copy()
            def stop(self,value):self.stopped.append(value);self.units.discard(value)
        supervisor=Recovery()
        with worker_lock(lock),worker_lock(db):recover(db,supervisor)
        exported=store.get_job(db,job['id'])
        assert exported['status']=='interrupted' and exported['summary'] is None
        assert exported['attempts']==before['attempts']
        assert exported['provenance']['execution_transport']=='admin-systemd-v1'
        assert unit in supervisor.stopped and store.claim(db) is None
        stopped=supervisor.stopped.copy()
        recover(db,supervisor)
        assert supervisor.stopped==stopped  # A second startup neither repeats nor relaunches it.
    finally:
        selector.close()
        if process.poll() is None:process.kill();process.wait(timeout=5)
        process.stdout.close();process.stderr.close()
