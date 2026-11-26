import importlib.util
import json
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
