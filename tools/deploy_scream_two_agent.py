"""Explicit operator deployment for the existing trav/192.168.0.188 lab.

Default: offline plan. --apply requires root, a pinned source bundle, an idle
inference window and the existing sealed agent/gateway/dashboard installation.
Never starts an inference engine or changes a boot, driver, fan or power policy.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import uuid

HOME = Path('/home/trav')
ROOT = HOME/'p100-inference-lab'
AGENT = Path('/etc/systemd/system/p100-lab-engine-agent.service')
GATEWAY = HOME/'.config/systemd/user/p100-inference-gateway.service'
ROUTES = HOME/'.config/p100-gateway/routes.json'
GUARD = HOME/'.config/p100-gateway/gpu-engine-idle.sh'
PRODUCTION = ROOT/'compose.production.yaml'
MARKER = ROOT/'DEPLOYED_REVISION'
BASE = HOME/'inference/scream-flash-next/profile.json'
ORIGINAL_512 = HOME/'inference/scream-flash-next-512k/profile.json'
EXECUTABLE = HOME/'inference/scream-p100/build/split/strata-split-2'
PROFILE = HOME/'inference/scream-flash-next-two-agent'
UNIT = HOME/'.config/systemd/user/p100-lab-scream-two-agent.service'
CONTAINER = 'p100-inference-lab-lab-1'
ENV = {**os.environ, 'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','LC_ALL':'C'}
PLAN = dict(engine='scream-two-agent', context_per_client=262144, generation_slots=1,
            conversation_cache_mib=12288, max_output=8192, starts_inference=False,
            automatic_boot=False, replaces_512k=False, requires_operator_apply=True)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def run(*argv, input=None, timeout=120):
    return subprocess.run(list(map(str,argv)),input=input,text=True,capture_output=True,
                          check=True,timeout=timeout,env=ENV).stdout.strip()


def user(*argv):
    return run('/usr/sbin/runuser','-u','trav','--','/usr/bin/env',
        'XDG_RUNTIME_DIR=/run/user/1000','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',*argv)


def atomic(path,content,mode,uid=0,gid=0):
    temporary=path.with_name(path.name+'.two-agent-'+uuid.uuid4().hex)
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(),mode);os.fchown(stream.fileno(),uid,gid)
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


RPC = '''import json,os,sys,urllib.request,urllib.error
a=json.load(sys.stdin);headers={}
if a.get('auth',True):headers['Authorization']='Bearer '+open(os.environ['LAB_API_TOKEN_FILE']).read().strip()
body=None
if 'body' in a:body=json.dumps(a['body']).encode();headers['Content-Type']='application/json'
r=urllib.request.Request('http://127.0.0.1:8000'+a['path'],data=body,headers=headers,method=a.get('method','GET'))
try:s=urllib.request.urlopen(r,timeout=12)
except urllib.error.HTTPError as e:s=e
with s:print(json.dumps({'status':s.status,'data':json.load(s)}))
'''


def rpc(container,path,**options):
    return json.loads(run('docker','exec','-i',container,'python','-c',RPC,
                         input=json.dumps(dict(path=path,**options)),timeout=25))


def data(container,path):
    result=rpc(container,path)
    assert result['status']==200,'Authenticated dashboard read failed'
    return result['data']


def compose(files):
    return ['docker','compose','-p','p100-inference-lab',*[value for path in files for value in ('-f',str(path))]]


def properties(unit,system=False):
    args=['/usr/bin/systemctl',*([] if system else ['--user']),'show',unit,
          '-p','ActiveState','-p','MainPID','-p','UnitFileState']
    output=run(*args) if system else user(*args)
    return dict(line.split('=',1) for line in output.splitlines() if '=' in line)


def idle(units):
    assert all(properties(unit)['ActiveState'] in ('inactive','failed') for unit in units), 'Stop inference through the dashboard first; nothing was stopped'
    assert not run('nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'), 'GPU compute is active; nothing was stopped'
    rows=run('nvidia-smi','--query-gpu=temperature.gpu,power.limit,memory.free,utilization.gpu','--format=csv,noheader,nounits').splitlines()
    values=[[float(v.strip()) for v in row.split(',')] for row in rows]
    assert len(values)==2 and all(t<60 and p==150 and free>=14000 and util<10 for t,p,free,util in values), 'Wait for cool idle GPUs at 150W'
    available=next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    assert available>=8*1024*1024,'Host RAM reserve is unavailable'


def gateway_snapshot():
    code=f'''import json,urllib.request
c=json.load(open({str(ROUTES)!r}));key=open(c['key_file']).read().strip()
o=urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
r=urllib.request.Request('http://192.168.0.188:8090/v1/models',headers={{'Authorization':'Bearer '+key}})
with o.open(r,timeout=10) as response:print(response.read().decode())
'''
    return json.loads(user('/usr/bin/python3','-I','-c',code))['data']


def replace_module(unit,module,launcher):
    old='-m '+module
    assert unit.count(old)==1,'Unexpected service launcher; review before deployment'
    return unit.replace(old,'-I '+str(launcher))


def configured_python(unit,home='/home/trav'):
    """Resolve the admitted user unit's %h before invoking outside systemd.

    Keep the venv interpreter path: resolving its symlink would lose its venv.
    """
    commands=[line.split('=',1)[1] for line in unit.splitlines() if line.startswith('ExecStart=')]
    if len(commands)!=1:raise ValueError('Expected one gateway service command')
    arguments=shlex.split(commands[0])
    executable=arguments[0].replace('%h',str(home).replace('\\','/'))
    path=PurePosixPath(executable)
    if '%' in executable or not path.is_absolute() or not re.fullmatch(r'python(?:3(?:\.\d+)?)?',path.name):
        raise ValueError('Unsupported gateway interpreter path; review the user unit')
    return executable


def engine_snapshot(container,configured,attempts=12):
    """A busy agent can transiently omit profile metadata; never pin that as baseline."""
    for attempt in range(attempts):
        items=data(container,'/api/engines')['items']
        present={item['id']:item for item in items}
        if all(name in present and present[name].get('state')!='unavailable' for name in configured):
            return items
        if attempt+1<attempts:time.sleep(1)
    raise RuntimeError('Configured engine status remained unavailable; no baseline accepted')


def preserve_failed_profile(evidence,builder,identifier,owner=1000,report_owner=0):
    """Quarantine only this installer's verified, rolled-back, unused artifact.

    No recursive deletion or overwrite; all original bytes/permissions survive.
    """
    assert re.fullmatch(r'[a-f0-9]{10}',identifier),'Invalid recovery identifier'
    backup=Path(evidence['backup'])
    assert backup.parent==ROOT and backup.name.startswith('two-agent-backup-') and not backup.is_symlink(),'Unexpected recovery evidence path'
    assert backup.stat().st_uid==report_owner and not stat.S_IMODE(backup.stat().st_mode)&0o077,'Recovery evidence must remain private'
    record=backup/'acceptance.json'
    assert record.is_file() and not record.is_symlink() and record.stat().st_uid==report_owner
    report=json.loads(record.read_text())
    assert report.get('passed') is False and report.get('rollback_verified') is True,'Recovery requires a verified rollback'
    assert report.get('revision')==evidence['revision'],'Recovery revision changed'
    assert PROFILE.is_dir() and not PROFILE.is_symlink() and PROFILE.stat().st_uid==owner
    assert not stat.S_IMODE(PROFILE.stat().st_mode)&0o077,'Failed profile permissions changed'
    assert PROFILE.resolve().parent==(HOME/'inference').resolve(),'Recovery target escaped inference directory'
    assert not UNIT.exists() and not UNIT.is_symlink(),'An installed unit already refers to this profile'
    assert {p.name for p in PROFILE.iterdir()}=={'profile.json','inference-key','logs'},'Unexpected files in failed profile; preserve for review'
    for name in ('profile.json','inference-key'):
        path=PROFILE/name;info=path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid==owner and not stat.S_IMODE(info.st_mode)&0o077
        assert sha(path.read_bytes())==evidence[name+'_sha256'],'Failed profile changed after diagnostic'
    logs=PROFILE/'logs'
    assert logs.is_dir() and not logs.is_symlink() and logs.stat().st_uid==owner and not any(logs.iterdir()),'Failed profile has inference logs; review before recovery'
    key=(PROFILE/'inference-key').read_text().strip()
    assert json.loads((PROFILE/'profile.json').read_text())==builder(json.loads(BASE.read_text()),PROFILE,key),'Failed artifact differs from the admitted recipe'
    destination=PROFILE.with_name(PROFILE.name+'.failed-'+identifier)
    assert not destination.exists() and not destination.is_symlink(),'Recovery archive already exists'
    PROFILE.rename(destination)
    return destination


def apply(package,manifest_sha):
    import fcntl
    import grp
    import pwd
    assert os.geteuid()==0,'Operator installation requires sudo'
    assert pwd.getpwnam('trav').pw_uid==1000 and grp.getgrnam('p100-lab-api').gr_gid==10001
    assert not grp.getgrnam('p100-lab-api').gr_mem,'Review API group membership before installation'
    os.umask(0o077)
    manifest_bytes=(package/'manifest.json').read_bytes()
    assert sha(manifest_bytes)==manifest_sha,'Manifest changed; review before running'
    manifest=json.loads(manifest_bytes)
    expected={str(p) for p in (PRODUCTION,MARKER,AGENT,GATEWAY,ROUTES,GUARD,BASE,ORIGINAL_512,EXECUTABLE)}
    assert set(manifest['expected_files'])==expected,'Unexpected installation scope'
    for filename,digest in manifest['expected_files'].items():
        p=Path(filename)
        assert not p.is_symlink() and sha(p.read_bytes())==digest,'Existing deployment changed; nothing was installed'
    archive=(package/'source.tar').read_bytes()
    assert sha(archive)==manifest['source_sha256'],'Source bundle changed'
    assert not UNIT.exists() and not UNIT.is_symlink(),'Two-agent unit already exists; no existing work will be overwritten'
    recovery=manifest.get('recover_failed_profile')
    if PROFILE.exists() or PROFILE.is_symlink():
        assert recovery is not None,'Two-agent profile already exists; reviewed recovery evidence required'
    lock=os.open('/run/lock/p100-two-agent-deployment.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    identifier=uuid.uuid4().hex[:10]
    runtime=Path('/opt')/('p100-lab-two-agent-'+identifier)
    backup=ROOT/('two-agent-backup-'+identifier)
    runtime.mkdir(mode=0o755);runtime.chmod(0o755);backup.mkdir(mode=0o700)
    source=runtime/'source';source.mkdir(mode=0o755);source.chmod(0o755)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        assert all(m.isfile() or m.isdir() for m in tar.getmembers()),'Unexpected archive member'
        tar.extractall(source,filter='data')
    for p in source.rglob('*'):os.chmod(p,0o755 if p.is_dir() else 0o644);os.chown(p,0,0)
    sys.path.insert(0,str(source/'backend'))
    from lab.adapters import MANAGED_UNITS
    from lab.scream_profiles import build_two_agent_profile,ENGINE_ID,PORT,ALIAS,LABEL,SERVING_PROFILE
    old_units=[unit for key,unit in MANAGED_UNITS.items() if key!=ENGINE_ID]
    idle(old_units)
    for port in (PORT,):
        import socket
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    before=json.loads(run('docker','inspect',CONTAINER))[0]
    assert before['State']['Running']
    old_files=before['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
    assert str(PRODUCTION) in old_files
    compose_hashes={p:sha(Path(p).read_bytes()) for p in old_files}
    resolved=json.loads(run(*compose(old_files),'config','--format','json'))['services']['lab']
    assert resolved['image']==before['Config']['Image'],'Pending image change; review before deployment'
    actual_env=dict(value.split('=',1) for value in before['Config']['Env'])
    assert all(value is None or str(value)==actual_env.get(key) for key,value in resolved.get('environment',{}).items()),'Pending environment change; review before deployment'
    gateway_before={row['id']:row for row in gateway_snapshot()}
    others={name:json.loads(run('docker','inspect',name))[0]['State']['StartedAt']
            for name in run('docker','ps','--format','{{.Names}}').splitlines() if name!=CONTAINER}
    docker_started=run('systemctl','show','docker.service','-p','ActiveEnterTimestampMonotonic','--value')
    fan_pid=properties('p100-lab-fans.service',True)['MainPID']
    power_before=run('nvidia-smi','--query-gpu=uuid,power.limit,persistence_mode','--format=csv,noheader')
    state_before={unit:properties(unit) for unit in old_units}
    baseline=dict(presets=data(CONTAINER,'/api/models')['presets'],
                  benchmarks=data(CONTAINER,'/api/benchmarks')['items'],
                  native_ids={r['id'] for r in data(CONTAINER,'/api/native-benchmarks')['items']})
    history=Path(next(m['Source'] for m in before['Mounts'] if m['Destination']=='/native-history'))/'native.sqlite3'
    history_sha=sha(history.read_bytes())
    fan_before=data(CONTAINER,'/api/fan-controls')
    old_routes=json.loads(ROUTES.read_text())
    agent_text=AGENT.read_text();gateway_text=GATEWAY.read_text()
    gateway_python=configured_python(gateway_text)
    user(gateway_python,'-I','-c','import fastapi,httpx,pydantic')
    command=next(line.split('=',1)[1] for line in agent_text.splitlines() if line.startswith('ExecStart='))
    arguments=shlex.split(command);config_path=Path(arguments[arguments.index('--config')+1])
    assert str(config_path).startswith('/opt/p100-lab-') and config_path.name=='agent-config.json' and not config_path.is_symlink()
    agent_config=json.loads(config_path.read_text())
    configured={'kmic'} | (set(agent_config) & set(MANAGED_UNITS))
    original_engines=engine_snapshot(CONTAINER,configured)
    assert ENGINE_ID not in agent_config and ENGINE_ID not in old_routes['upstreams'] and ALIAS not in old_routes['models']
    saved={p:(p.read_bytes(),stat.S_IMODE(p.stat().st_mode),p.stat().st_uid,p.stat().st_gid) for p in (AGENT,GATEWAY,ROUTES,GUARD,MARKER)}
    for p,entry in saved.items():atomic(backup/p.name,*entry)
    atomic(backup/'agent-config.json',config_path.read_bytes(),0o600)
    atomic(backup/'old-compose-files.json',json.dumps(old_files).encode(),0o600)
    backup_code="""import sqlite3,tempfile,os,sys,shutil
fd,p=tempfile.mkstemp();os.close(fd)
try:
 a=sqlite3.connect('file:/data/lab.sqlite3?mode=ro',uri=True);b=sqlite3.connect(p)
 try:a.backup(b)
 finally:b.close();a.close()
 with open(p,'rb') as f:shutil.copyfileobj(f,sys.stdout.buffer)
finally:os.unlink(p)
"""
    with (backup/'lab.sqlite3').open('xb') as stream:
        subprocess.run(['docker','exec',CONTAINER,'python','-c',backup_code],stdout=stream,stderr=subprocess.PIPE,check=True,timeout=120)
    with sqlite3.connect(backup/'lab.sqlite3') as db:assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    candidate_data=runtime/'candidate-data';candidate_data.mkdir(mode=0o700);os.chown(candidate_data,10001,10001)
    import shutil
    shutil.copyfile(backup/'lab.sqlite3',candidate_data/'lab.sqlite3');os.chown(candidate_data/'lab.sqlite3',10001,10001);os.chmod(candidate_data/'lab.sqlite3',0o600)
    candidate='p100-two-agent-accept-'+identifier
    image='p100-inference-lab:two-agent-'+manifest['revision'][:7]+'-'+identifier
    overlay=ROOT/('compose.two-agent-'+identifier+'.yaml')
    report=dict(revision=manifest['revision'],passed=False,backup=str(backup))
    changed=False;switched=False;created_unit=False

    def check(container):
        engines=engine_snapshot(container,configured|{ENGINE_ID})
        previous={row['id']:row for row in original_engines}
        assert {row['id'] for row in engines}==set(previous)|{ENGINE_ID}
        for row in engines:
            if row['id'] in previous:
                assert row['unit']==previous[row['id']]['unit']
                assert row.get('serving_profile')==previous[row['id']].get('serving_profile')
        entry=next(row for row in engines if row['id']==ENGINE_ID)
        assert entry['controls_available'] and entry['state']=='inactive' and not entry['ready']
        assert entry['serving_profile']==SERVING_PROFILE
        assert data(container,'/api/models')['presets']==baseline['presets'],'Saved presets changed'
        assert data(container,'/api/benchmarks')['items']==baseline['benchmarks'],'Benchmark history changed'
        assert {r['id'] for r in data(container,'/api/native-benchmarks')['items']}==baseline['native_ids'],'Native history changed'
        current=data(container,'/api/fan-controls')
        assert current['enabled']==fan_before['enabled']
        assert {k:v['mode'] for k,v in current['channels'].items()}=={k:v['mode'] for k,v in fan_before['channels'].items()}
        assert rpc(container,'/api/engines',auth=False)['status']==401
        assert rpc(container,'/api/engines/'+ENGINE_ID+'/control',method='POST',body={'action':'start'})['status']==403

    try:
        print('Building the reviewed dashboard; the current dashboard stays running.',flush=True)
        run('docker','build','-t',image,source,timeout=900)
        idle(old_units)
        for filename,digest in manifest['expected_files'].items():assert sha(Path(filename).read_bytes())==digest,'Deployment changed during build'
        assert all(sha(Path(p).read_bytes())==value for p,value in compose_hashes.items()),'Compose overlay changed during build'
        if PROFILE.exists():
            archived=preserve_failed_profile(recovery,build_two_agent_profile,identifier)
            report['preserved_failed_profile']=str(archived)
            print('Preserved failed profile: '+str(archived),flush=True)
        PROFILE.mkdir(mode=0o700);os.chown(PROFILE,1000,1000)
        (PROFILE/'logs').mkdir(mode=0o700);os.chown(PROFILE/'logs',1000,1000)
        key=secrets.token_urlsafe(48)
        profile=build_two_agent_profile(json.loads(BASE.read_text()),PROFILE,key)
        atomic(PROFILE/'profile.json',json.dumps(profile,indent=2).encode(),0o600,1000,1000)
        atomic(PROFILE/'inference-key',(key+'\n').encode(),0o600,1000,1000)
        contents=(source/'deploy/p100-lab-scream-two-agent.service.example').read_bytes()
        atomic(UNIT,contents,0o644,1000,1000);created_unit=True
        config={**agent_config,ENGINE_ID:dict(unit_file=str(UNIT),unit_sha256=sha(contents),
            endpoint=f'http://127.0.0.1:{PORT}',upstream_token_file=str(PROFILE/'inference-key'),
            model_label=LABEL,serving_profile=SERVING_PROFILE)}
        new_config=runtime/'agent-config.json';atomic(new_config,json.dumps(config,indent=2).encode(),0o640,0,10001)
        # Validate in the actual unprivileged account/group, not as root.
        validation=f"import sys,json;sys.path.insert(0,{str(source/'backend')!r});from lab.engine_agent import Controller;Controller(json.load(open({str(new_config)!r})))"
        run('/usr/sbin/runuser','-u','trav','-g','p100-lab-api','--','/usr/bin/python3','-I','-c',validation)
        for module,name in [('lab.engine_agent','agent'),('lab.inference_gateway','gateway')]:
            text=f"import sys\nsys.path.insert(0,{str(source/'backend')!r})\nfrom {module} import main\nmain()\n"
            atomic(runtime/(name+'.py'),text.encode(),0o644)
        new_agent=replace_module(agent_text,'lab.engine_agent',runtime/'agent.py')
        assert new_agent.count(str(config_path))==1
        new_agent=new_agent.replace(str(config_path),str(new_config))
        new_gateway=replace_module(gateway_text,'lab.inference_gateway',runtime/'gateway.py')
        routes=json.loads(json.dumps(old_routes))
        routes['upstreams'][ENGINE_ID]=dict(url=f'http://127.0.0.1:{PORT}',key_file=str(PROFILE/'inference-key'))
        routes['models'][ALIAS]=dict(engine=ENGINE_ID,name=LABEL,context=262144,max_output=8192,reasoning=True,vision=True)
        test_routes=runtime/'gateway-routes.json';atomic(test_routes,json.dumps(routes).encode(),0o600,1000,1000)
        validation=f"import sys,json;sys.path.insert(0,{str(source/'backend')!r});from lab.inference_gateway import Configuration,create_app;create_app(Configuration.model_validate(json.load(open({str(test_routes)!r}))))"
        user(gateway_python,'-I','-c',validation)
        changed=True
        for p,value in [(AGENT,new_agent.encode()),(GATEWAY,new_gateway.encode()),(ROUTES,json.dumps(routes,indent=2).encode()),
                        (GUARD,(source/'deploy/gpu-engine-idle.sh').read_bytes())]:atomic(p,value,*saved[p][1:])
        user('/usr/bin/systemctl','--user','daemon-reload')
        run('systemctl','daemon-reload');run('systemctl','restart','p100-lab-engine-agent.service')
        user('/usr/bin/systemctl','--user','restart','p100-inference-gateway.service')
        for attempt in range(20):
            try:
                models={row['id']:row for row in gateway_snapshot()}
                assert set(models)==set(gateway_before)|{ALIAS}
                assert all(models[key]==value for key,value in gateway_before.items())
                assert models[ALIAS]['max_model_len']==262144 and models[ALIAS]['limits']['max_output_tokens']==8192
                assert models[ALIAS]['available'] is False
                break
            except Exception:
                if attempt==19:raise
                time.sleep(1)
        pid=properties('p100-lab-engine-agent.service',True)['MainPID']
        process=dict(line.split(':',1) for line in Path('/proc',pid,'status').read_text().splitlines() if ':' in line)
        assert process['Uid'].split()[0]=='1000' and process['Gid'].split()[0]=='10001' and int(process['CapEff'].strip(),16)==0
        args=['docker','run','-d','--name',candidate,'--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true',
              '--tmpfs','/tmp:size=16m,noexec,nosuid','--memory','512m','--cpus','1','--pids-limit','100','--runtime','nvidia']
        for variable in before['Config']['Env']:args+=['-e',variable]
        for mount in before['Mounts']:
            dest=mount['Destination'];origin=str(candidate_data) if dest=='/data' else mount['Source']
            args+=['--mount',f'type=bind,src={origin},dst={dest}'+('' if dest=='/data' else ',readonly')]
        run(*args,image)
        for attempt in range(30):
            try:check(candidate);break
            except Exception:
                if attempt==29:raise
                time.sleep(1)
        print('Authenticated profile, queued/cache metadata, denied controls and preserved history verified.',flush=True)
        atomic(overlay,json.dumps({'services':{'lab':{'image':image,'labels':{'p100.lab.two-agent-profile':manifest['revision']}}}},indent=2).encode(),0o644)
        files=[*old_files,str(overlay)]
        run(*compose(files),'config','--quiet')
        idle(old_units)
        switched=True
        run(*compose(files),'up','-d','--no-build','--no-deps','--pull','never','--wait','--wait-timeout','90','lab',timeout=150)
        check(CONTAINER)
        assert {unit:properties(unit) for unit in old_units}==state_before
        assert properties('p100-lab-fans.service',True)['MainPID']==fan_pid
        assert run('systemctl','show','docker.service','-p','ActiveEnterTimestampMonotonic','--value')==docker_started
        assert all(json.loads(run('docker','inspect',name))[0]['State']['StartedAt']==value for name,value in others.items())
        assert run('nvidia-smi','--query-gpu=uuid,power.limit,persistence_mode','--format=csv,noheader')==power_before
        assert sha(BASE.read_bytes())==manifest['expected_files'][str(BASE)] and sha(ORIGINAL_512.read_bytes())==manifest['expected_files'][str(ORIGINAL_512)]
        assert sha(history.read_bytes())==history_sha,'Read-only history source changed'
        atomic(MARKER,(manifest['revision']+'\n').encode(),*saved[MARKER][1:])
        report['passed']=True
        print('Two-agent queued 256K option installed and left stopped. Existing 512K option, tokens, history, fan service and 150W policy preserved.',flush=True)
        print('Private backup/report: '+str(backup),flush=True)
    except BaseException as error:
        import traceback
        diagnostic=traceback.format_exc()
        if isinstance(error,subprocess.CalledProcessError):diagnostic+='\n'+str(error.stdout)+'\n'+str(error.stderr)
        atomic(backup/'failure.txt',diagnostic.encode(),0o600)
        report['error_type']=type(error).__name__
        try:
            if created_unit:user('/usr/bin/systemctl','--user','stop',UNIT.name)
            if switched:run(*compose(old_files),'up','-d','--no-build','--no-deps','--pull','never','--wait','--wait-timeout','90','lab',timeout=150)
            if changed:
                for path,value in saved.items():atomic(path,*value)
                user('/usr/bin/systemctl','--user','daemon-reload');run('systemctl','daemon-reload')
                run('systemctl','restart','p100-lab-engine-agent.service')
                user('/usr/bin/systemctl','--user','restart','p100-inference-gateway.service')
            if created_unit:
                assert sha(UNIT.read_bytes())==sha((source/'deploy/p100-lab-scream-two-agent.service.example').read_bytes())
                UNIT.unlink();user('/usr/bin/systemctl','--user','daemon-reload')
            report['rollback_verified']=not changed or all(p.read_bytes()==value[0] for p,value in saved.items())
            print('Previous dashboard and integration restored; no inference engine was started.',flush=True)
        except Exception:
            report['rollback_verified']=False
            print('Rollback requires inspection. Do not retry automatically.',flush=True)
        raise RuntimeError('Installation stopped; private report records the failure type') from None
    finally:
        subprocess.run(['docker','rm','-f',candidate],capture_output=True,timeout=30)
        atomic(backup/'acceptance.json',json.dumps(report,indent=2).encode(),0o600)
        os.close(lock)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--window-confirmed',action='store_true')
    parser.add_argument('--package',type=Path)
    parser.add_argument('--manifest-sha')
    args=parser.parse_args()
    if not args.apply:
        print(json.dumps(PLAN,indent=2));return
    if not args.window_confirmed or not args.package or not args.manifest_sha or len(args.manifest_sha)!=64:
        parser.error('Apply requires an explicit idle window, package and reviewed manifest SHA-256')
    try:apply(args.package.resolve(),args.manifest_sha)
    except Exception as error:
        detail=str(error) if isinstance(error,(AssertionError,RuntimeError)) else type(error).__name__
        print('Installation refused or stopped: '+detail+'; inspect the private report if created.',flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':main()
