import copy
import os

import pytest

from lab.scream_profiles import build_two_agent_profile, SERVING_PROFILE, ALIAS


def base():
    return dict(api_key='old-key-'+'x'*40, model_name='existing-512k', port=8087,
        exe='/fixed/engine', cwd='/fixed/source', tokenizer='/existing/tokenizer',
        args=['--native','/existing/model.gguf','--mtp','/existing/drafter',
              '--spec','4','--lookup','15','--batch','1','--max-context','524288',
              '--conversation-cache-mib','4000'],
        env=dict(FNX_EXPERT_RAM_GIB='29.0',FNX_EXPERT_DYN_GIB='13',FNX_PLE_RAM='1',
                 FNX_ROPE_SCALE='2',FNX_YARN_ORIG_CTX='262144',FNX_PF_STAGE_ROWS='256',
                 FNX_PF_STAGE_ALL='1',FNX_PF_STAGE_MIB='1024',FNX_PF_TAIL_MIB='3000',FNX_ROUTE_PRIOR='0'))


def test_queued_profile_has_tested_values_and_preserves_original_and_artifacts(tmp_path):
    original=base(); before=copy.deepcopy(original)
    profile=build_two_agent_profile(original,tmp_path,'new-key-'+'n'*40)
    assert original==before
    assert profile['model_name']==ALIAS and profile['port']==8088
    assert profile['api_key']!=original['api_key']
    for field in ['exe','cwd','tokenizer']: assert profile[field]==original[field]
    for flag,value in [('--batch','1'),('--max-context','262144'),('--conversation-cache-mib','12288')]:
        assert profile['args'][profile['args'].index(flag)+1]==value
    assert profile['args'][1]=='/existing/model.gguf'
    assert profile['env']['FNX_EXPERT_RAM_GIB']=='34.0' and profile['env']['FNX_PLE_RAM']=='0'
    assert not {'FNX_EXPERT_DYN_GIB','FNX_ROPE_SCALE','FNX_YARN_ORIG_CTX'} & profile['env'].keys()
    assert SERVING_PROFILE['max_output']==8192 and SERVING_PROFILE['generation_slots']==1


@pytest.mark.parametrize('change',['duplicate-context','slots','spec','prefill','same-key'])
def test_conflicting_or_untested_base_refused(tmp_path,change):
    original=base(); key='new-key-'+'n'*40
    if change=='duplicate-context':original['args']+=['--max-context','262144']
    if change=='slots':original['args']+=['--slots','2']
    if change=='spec':original['args'][original['args'].index('--spec')+1]='8'
    if change=='prefill':original['env']['FNX_PF_TAIL_MIB']='0'
    if change=='same-key':key=original['api_key']
    with pytest.raises(ValueError):build_two_agent_profile(original,tmp_path,key)


@pytest.mark.skipif(os.name!='posix',reason='Host agent uses Unix sockets')
def test_serving_metadata_is_bounded_and_backwards_compatible():
    from lab.engine_agent import serving_profile
    old={k:v for k,v in SERVING_PROFILE.items() if k not in (
        'generation_slots','conversation_cache_mib','client_default_thinking')}
    assert serving_profile(old)==old
    assert serving_profile(SERVING_PROFILE)==SERVING_PROFILE
    for change in [dict(generation_slots=True),dict(generation_slots=8),dict(conversation_cache_mib=-1),
                   dict(client_default_thinking='false'),dict(command='id')]:
        with pytest.raises(ValueError):serving_profile({**SERVING_PROFILE,**change})
