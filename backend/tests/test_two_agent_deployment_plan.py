import importlib.util
import json
import os
from pathlib import Path

import pytest

from lab.scream_profiles import SERVING_PROFILE


def load():
    path=Path(__file__).resolve().parents[2]/'tools/deploy_scream_two_agent.py'
    spec=importlib.util.spec_from_file_location('two_agent_deploy',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_default_deployment_plan_does_not_touch_host(monkeypatch,capsys):
    module=load()
    monkeypatch.setattr('sys.argv',['deploy_scream_two_agent'])
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('Default plan ran a host command'))
    monkeypatch.setattr(Path,'read_bytes',lambda *a,**k:pytest.fail('Default plan read a private file'))
    module.main()
    result=json.loads(capsys.readouterr().out)
    assert result['starts_inference'] is False and result['replaces_512k'] is False
    assert result['generation_slots']==SERVING_PROFILE['generation_slots']
    assert result['conversation_cache_mib']==SERVING_PROFILE['conversation_cache_mib']


def test_apply_requires_reviewed_bundle_and_idle_window(monkeypatch):
    module=load();monkeypatch.setattr('sys.argv',['deploy_scream_two_agent','--apply'])
    monkeypatch.setattr(module,'apply',lambda *a:pytest.fail('Unconfirmed apply'))
    with pytest.raises(SystemExit):module.main()


@pytest.mark.parametrize('command,expected',[
    ('%h/inference/p100-gateway/venv/bin/python','/home/trav/inference/p100-gateway/venv/bin/python'),
    ('/home/trav/inference/p100-gateway/venv/bin/python','/home/trav/inference/p100-gateway/venv/bin/python'),
    ('/usr/bin/python3.12','/usr/bin/python3.12'),
])
def test_systemd_home_is_expanded_without_resolving_away_venv(command,expected):
    unit=f'[Service]\nExecStart={command} -m lab.inference_gateway --config %h/routes.json\n'
    assert load().configured_python(unit)==expected
    assert '%h/routes.json' in unit


@pytest.mark.parametrize('command',['%x/bin/python','python3','/bin/sh'])
def test_unsupported_interpreter_specifiers_or_shell_are_refused(command):
    with pytest.raises(ValueError):load().configured_python('ExecStart='+command+' -m lab.inference_gateway')


def test_baseline_retries_transient_missing_metadata_but_allows_unconfigured_engines(monkeypatch):
    module=load();calls=[]
    stable=[dict(id='kmic',state='inactive',serving_profile={'alias':'original'}),dict(id='pxa',state='unavailable')]
    def read(*args):
        calls.append(args)
        return {'items':[dict(id='kmic',state='unavailable')]} if len(calls)==1 else {'items':stable}
    monkeypatch.setattr(module,'data',read);monkeypatch.setattr(module.time,'sleep',lambda *_:None)
    assert module.engine_snapshot('candidate',{'kmic'})==stable
    assert len(calls)==2
    monkeypatch.setattr(module,'data',lambda *args:{'items':[dict(id='kmic',state='unavailable')]})
    assert module.engine_snapshot('candidate',{'kmic'},attempts=2)==[dict(id='kmic',state='unavailable')]


@pytest.fixture
def recovery(tmp_path,monkeypatch):
    if os.name!='posix':pytest.skip('Recovery checks Ubuntu ownership and permissions')
    module=load();owner=os.getuid()
    home=tmp_path/'home';root=home/'lab';root.mkdir(parents=True)
    profile=home/'inference/scream-two-agent';profile.mkdir(parents=True,mode=0o700)
    (profile/'logs').mkdir(mode=0o700)
    base=home/'base.json';base.write_text('{}')
    def builder(base,path,key):return {'model_name':'two-agent','api_key':key,'log':str(path/'logs/engine.log')}
    (profile/'profile.json').write_text(json.dumps(builder({},profile,'test-key-'+'x'*40)))
    (profile/'inference-key').write_text('test-key-'+'x'*40+'\n')
    for name in ['profile.json','inference-key']:(profile/name).chmod(0o600)
    backup=root/'two-agent-backup-old';backup.mkdir(mode=0o700)
    (backup/'acceptance.json').write_text(json.dumps({'revision':'old-revision','passed':False,'rollback_verified':True}))
    evidence={'backup':str(backup),'revision':'old-revision',**{
        name+'_sha256':module.sha((profile/name).read_bytes()) for name in ['profile.json','inference-key']}}
    for name,value in dict(HOME=home,ROOT=root,PROFILE=profile,BASE=base,UNIT=home/'new.service').items():
        monkeypatch.setattr(module,name,value)
    return module,evidence,builder,owner


def test_verified_unused_failed_profile_is_preserved_byte_for_byte(recovery):
    module,evidence,builder,owner=recovery
    before={name:(module.PROFILE/name).read_bytes() for name in ['profile.json','inference-key']}
    destination=module.preserve_failed_profile(evidence,builder,'abc123abcd',owner=owner,report_owner=owner)
    assert destination.parent==module.PROFILE.parent and not module.PROFILE.exists()
    assert {name:(destination/name).read_bytes() for name in before}==before
    assert (destination/'logs').is_dir()


@pytest.mark.parametrize('change',['unverified-rollback','changed-key','unexpected-file','inference-log','installed-unit','existing-archive'])
def test_recovery_never_moves_changed_or_in_use_artifacts(recovery,change):
    module,evidence,builder,owner=recovery
    if change=='unverified-rollback':
        (Path(evidence['backup'])/'acceptance.json').write_text(json.dumps({'revision':'old-revision','passed':False,'rollback_verified':False}))
    if change=='changed-key':(module.PROFILE/'inference-key').write_text('changed')
    if change=='unexpected-file':(module.PROFILE/'extra.gguf').write_bytes(b'GGUF')
    if change=='inference-log':(module.PROFILE/'logs/engine.log').write_text('Inference was run')
    if change=='installed-unit':module.UNIT.write_text('Existing work')
    if change=='existing-archive':module.PROFILE.with_name(module.PROFILE.name+'.failed-abc123abcd').mkdir()
    with pytest.raises(AssertionError):module.preserve_failed_profile(evidence,builder,'abc123abcd',owner=owner,report_owner=owner)
    assert module.PROFILE.is_dir() and (module.PROFILE/'profile.json').is_file()
