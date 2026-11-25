"""No GPU, systemd mutation or real inference: experiment runner contract tests."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SPEC = importlib.util.spec_from_file_location('scream_concurrency', Path(__file__).resolve().parents[2]/'tools/scream_concurrency.py')
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def sample():
    return dict(gpus=[dict(uuid=str(i), name='Tesla P100-PCIE-16GB', temperature_c=45,
                          limit_w=150, used_mib=100, utilization=0) for i in range(2)],
                available_bytes=12*2**30, swap_used_bytes=0)


def test_matrix_distinguishes_capacity_from_short_screen():
    screen = bench.matrix()
    assert len(screen)==20 and all(c['target_prompt_tokens']==8192 for c in screen)
    assert {c['slots'] for c in screen}=={1,2,3,4}
    for case in bench.matrix(tier='capacity'):
        assert case['target_prompt_tokens']+case['output_tokens'] < case['context']
        assert case['repeats']==3 and case['turns']==2
        assert case['concurrency']==sorted({1,case['slots'],2*case['slots']})


def test_capacity_probe_is_near_limit_but_not_a_repeated_performance_result():
    case=bench.matrix(slots=[2],contexts=[262144],tier='probe')[0]
    assert case['target_prompt_tokens']==262144-2048
    assert case['concurrency']==[2] and case['repeats']==case['turns']==1
    assert case['output_tokens']==256


def test_default_cli_is_offline(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['scream_concurrency'])
    monkeypatch.setattr(bench.subprocess, 'run', lambda *a,**k: pytest.fail('Subprocess in offline plan'))
    monkeypatch.setattr(bench.socket, 'socket', lambda *a,**k: pytest.fail('Socket in offline plan'))
    bench.main()
    assert json.loads(capsys.readouterr().out)['execution_enabled'] is False


def test_execution_requires_explicit_window_and_paths(monkeypatch):
    monkeypatch.setattr('sys.argv', ['scream_concurrency','--execute'])
    with pytest.raises(SystemExit):
        bench.main()


def test_matched_client_workload_is_explicit_in_offline_plan(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['scream_concurrency','--slots','1','4','--contexts','65536',
                                  '--tier','capacity','--clients','4','--prompt-tokens','8192'])
    bench.main()
    cases=json.loads(capsys.readouterr().out)['cases']
    assert len(cases)==2 and all(c['concurrency']==[4] for c in cases)
    assert all(c['target_prompt_tokens']-c['output_tokens']-256==8192 for c in cases)


def test_prompt_override_cannot_exceed_context_budget(monkeypatch):
    monkeypatch.setattr('sys.argv',['scream_concurrency','--contexts','32768','--tier','capacity',
                                  '--prompt-tokens','32768'])
    with pytest.raises(SystemExit):bench.main()


@pytest.mark.parametrize('failure', ['heat','power','ram','swap','card_count','model'])
def test_resource_guard_fails_closed(failure):
    state = sample()
    if failure=='heat': state['gpus'][0]['temperature_c']=75
    if failure=='power': state['gpus'][1]['limit_w']=250
    if failure=='ram': state['available_bytes']=5*2**30
    if failure=='swap': state['swap_used_bytes']=257*2**20
    if failure=='card_count': state['gpus'].pop()
    if failure=='model': state['gpus'][0]['name']='Other GPU'
    with pytest.raises(RuntimeError): bench.safe_sample(state,0,6)
    bench.safe_sample(sample(),0,6)


def test_nonfinite_gpu_telemetry_is_rejected(monkeypatch):
    monkeypatch.setattr(bench, 'fixed', lambda *a:'GPU-x, Tesla P100-PCIE-16GB, NaN, 30, 150, 30, 16384, 0')
    with pytest.raises(ValueError): bench.hardware()


def test_active_service_refuses_before_gpu_or_any_mutation(monkeypatch):
    calls=[]
    def fixed(*args):
        calls.append(args)
        return 'active'
    monkeypatch.setattr(bench,'fixed',fixed)
    with pytest.raises(RuntimeError, match='no service was stopped'): bench.idle_window()
    assert len(calls)==1 and calls[0][2]=='show'


def test_existing_compute_process_refuses(monkeypatch):
    monkeypatch.setattr(bench,'fixed',lambda *a:'inactive' if 'systemctl' in a[0] else '1234')
    with pytest.raises(RuntimeError,match='no process was stopped'): bench.idle_window()


def test_port_probe_reuses_closed_connections_but_refuses_listener(monkeypatch):
    monkeypatch.setattr(bench,'fixed',lambda *a:'inactive' if 'systemctl' in a[0] else '')
    monkeypatch.setattr(bench,'hardware',sample)
    with bench.socket.socket() as server:
        server.setsockopt(bench.socket.SOL_SOCKET,bench.socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',0))
        server.listen()
        monkeypatch.setattr(bench,'PORT',server.getsockname()[1])
        with pytest.raises(RuntimeError,match='already has a listener'): bench.idle_window()
    assert bench.idle_window()['available_bytes']==sample()['available_bytes']


def test_port_probe_accepts_time_wait_after_an_actual_connection(monkeypatch):
    monkeypatch.setattr(bench,'fixed',lambda *a:'inactive' if 'systemctl' in a[0] else '')
    monkeypatch.setattr(bench,'hardware',sample)
    with bench.socket.socket() as server:
        server.setsockopt(bench.socket.SOL_SOCKET,bench.socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',0))
        server.listen()
        address=server.getsockname()
        monkeypatch.setattr(bench,'PORT',address[1])
        with bench.socket.create_connection(address,timeout=2) as client:
            accepted,_=server.accept()
            with accepted:
                accepted.shutdown(bench.socket.SHUT_WR)  # Server actively closes first.
                assert client.recv(1)==b''
    assert bench.idle_window()['available_bytes']==sample()['available_bytes']


def test_profile_changes_only_test_identity_slots_context_and_rope(tmp_path):
    root=tmp_path/'scream'
    base=dict(exe=str(root/'build/split/strata-split-2'),cwd=str(root/'engine'),api_key='private-original',
              args=['--max-context','524288','--batch','1','--spec','4','--conversation-cache-mib','4000'],
              env={'FNX_ROPE_SCALE':'2','FNX_YARN_ORIG_CTX':'262144','FNX_EXPERT_RAM_GIB':'29.0'})
    original=copy.deepcopy(base)
    case=bench.matrix(slots=[2],contexts=[262144])[0]
    cfg=bench.profile_for(base,case,root,tmp_path,'new-private-key')
    assert base==original and cfg['args'][3]=='2' and cfg['args'][1]=='262144'
    assert cfg['args'][4:]==base['args'][4:]
    assert cfg['env']=={'FNX_EXPERT_RAM_GIB':'29.0'}
    assert cfg['api_key']=='new-private-key' and cfg['port']==18087
    case['context']=524288
    assert bench.profile_for(base,case,root,tmp_path,'new')['env']['FNX_ROPE_SCALE']=='2'
    base['exe']='/unexpected'
    with pytest.raises(ValueError): bench.profile_for(base,case,root,tmp_path,'new')


def test_stop_is_scoped_to_our_transient_unit(monkeypatch):
    calls=[]
    monkeypatch.setattr(bench.subprocess,'run',lambda *a,**k:calls.append(a[0]))
    with pytest.raises(ValueError): bench.stop_owned(None,'p100-lab-scream.service')
    assert calls==[]
    bench.stop_owned(None,'p100-concurrency-fixture.service')
    assert calls==[['/usr/bin/systemctl','--user','stop','p100-concurrency-fixture.service']]


@pytest.mark.parametrize('max_value,swap_value,accepted', [('12345','0',True),('max','0',False),('12345','max',False),('99999','0',False)])
def test_effective_cgroup_limits_are_checked(monkeypatch,tmp_path,max_value,swap_value,accepted):
    unit='p100-concurrency-fixture.service'
    cgroup=tmp_path/'cgroup'
    group=cgroup/'user.slice'/unit
    group.mkdir(parents=True)
    (group/'memory.max').write_text(max_value)
    (group/'memory.swap.max').write_text(swap_value)
    membership=tmp_path/'membership'
    membership.write_text('0::/user.slice/'+unit+'\n')
    real_path=Path
    monkeypatch.setattr(bench,'Path',lambda p:membership if p=='/proc/123/cgroup' else cgroup if p=='/sys/fs/cgroup' else real_path(p))
    monkeypatch.setattr(bench,'fixed',lambda *a:'123')
    if accepted:
        bench.verify_cgroup(unit,12345)
    else:
        with pytest.raises((ValueError,RuntimeError)):
            bench.verify_cgroup(unit,12345)


def test_prompt_is_calibrated_and_agents_have_distinct_prefixes():
    class Counter:
        def json(self,path,payload):
            assert path=='/v1/messages/count_tokens'
            return {'input_tokens':len(payload['messages'][0]['content'])//4}
    a,n=bench.prompt(Counter(),2048,'a'*16)
    b,m=bench.prompt(Counter(),2048,'b'*16)
    assert 2007<=n<=2048 and 2007<=m<=2048
    assert a[0]['content']!=b[0]['content']
    assert 'queue_item_0' in a[0]['content'] and 'queue_item_1' in a[0]['content']


def test_stream_ignores_role_frame_and_requires_usage_and_done(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers['Authorization']=='Bearer fixture-key'
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert payload['max_tokens']==256 and payload['stream_options']['include_usage']
            self.send_response(200)
            self.end_headers()
            for event in [{'choices':[{'delta':{'role':'assistant'}}]},
                          {'choices':[{'delta':{'content':'SESSION=x'}}]},
                          {'choices':[{'delta':{'content':' example'},'finish_reason':'length'}]},
                          {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':3}}]:
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    monkeypatch.setattr(bench,'PORT',server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        api=bench.API('fixture-key',threading.Event())
        row=api.generate([{'role':'user','content':'test'}],256,threading.Barrier(1),bench.time.monotonic()+10)
        assert row['text']=='SESSION=x example' and row['completion_tokens']==3
        assert row['ttft_s']>=0 and row['prompt_tokens']==100
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_summary_keeps_followups_separate_and_counts_wave_makespan():
    case=bench.matrix(slots=[2],contexts=[32768],tier='capacity')[0]
    case['concurrency']=[2]
    rows=[]
    for turn in (0,1):
        for client in (0,1):
            rows.append(dict(concurrency=2,turn=turn,trial=0,started=0,ended=10+client,
                first_generated_at=1,last_generated_at=10,ttft_s=1,wall_s=10+client,
                completion_tokens=100,requested_output_tokens=100))
    summary=bench.summarize([dict(case=case,passed=True,requests=rows)])
    assert len(summary)==2 and [r['turn'] for r in summary]==[0,1]
    assert summary[0]['aggregate_output_tokens_s_median']==pytest.approx(200/11)
    assert summary[0]['peak_overlapping_generated_streams']==2


def test_recommendations_exclude_screening_failed_and_short_outputs():
    row=dict(passed=True,tier='capacity',concurrency=2,slots=2,requests=6,
             output_budget_fraction_median=1,context=32768,turn=0,
             aggregate_output_tokens_s_median=20,ttft_s_p95=5)
    variants=[row,{**row,'tier':'screen'}, {**row,'passed':False},
              {**row,'output_budget_fraction_median':.2}]
    choices=bench.recommendations(variants)['comparisons']
    assert len(choices)==1 and choices[0]['best_aggregate']==row
    assert bench.recommendations(variants[1:])['comparisons']==[]


def test_recommendations_compare_equal_load_across_different_slot_counts():
    row=dict(passed=True,tier='capacity',concurrency=4,slots=1,requests=12,
             output_budget_fraction_median=1,context=65536,turn=0,
             initial_prompt_target=8192,requested_output_tokens=1024,
             aggregate_output_tokens_s_median=20,ttft_s_p95=10)
    faster={**row,'slots':4,'aggregate_output_tokens_s_median':22,'ttft_s_p95':20}
    unlike={**faster,'concurrency':8,'requests':24}
    choices=bench.recommendations([row,faster,unlike])['comparisons']
    matched=next(c for c in choices if c['clients']==4)
    assert matched['best_aggregate']['slots']==4
    assert matched['best_first_token_latency']['slots']==1


def test_multiturn_wave_keeps_agent_histories_independent(monkeypatch,tmp_path):
    histories=[]
    saved=[]
    monkeypatch.setattr(bench,'save',lambda path,value:saved.append(copy.deepcopy(value)))
    class FakeAPI:
        def generate(self,messages,output,barrier,deadline):
            barrier.wait(timeout=5)
            marker=messages[0]['content']
            histories.append(copy.deepcopy(messages))
            now=bench.time.monotonic()
            return dict(text='SESSION='+marker, prompt_tokens=100,completion_tokens=32,
                        started=now,ended=now+1,first_generated_at=now,last_generated_at=now+1,
                        ttft_s=0,wall_s=1)
        def cancel(self): pytest.fail('Valid wave cancelled')
    monkeypatch.setattr(bench,'prompt',lambda api,target,marker:([{'role':'user','content':marker}],100))
    case=bench.matrix(slots=[2],contexts=[32768],tier='capacity')[0]
    rows=bench.wave(FakeAPI(),2,case,0,tmp_path,threading.Event())
    assert len(rows)==4 and all(row['marker_ok'] for row in rows)
    assert len(saved)==2
    followups=[h for h in histories if len(h)==3]
    assert len(followups)==2
    for history in followups:
        assert history[1]['content']=='SESSION='+history[0]['content']


def test_candidate_startup_failure_always_cleans_only_owned_unit(monkeypatch,tmp_path):
    state=sample()
    state['available_bytes']=64*2**30
    root=tmp_path/'source'
    root.mkdir()
    base=dict(exe=str(root/'build/split/strata-split-2'),cwd=str(root/'engine'),
              args=['--max-context','32768','--batch','1'],env={})
    monkeypatch.setattr(bench,'idle_window',lambda:state)
    monkeypatch.setattr(bench,'hardware',lambda:state)
    monkeypatch.setattr(bench,'verify_cgroup',lambda *a:None)
    monkeypatch.setattr(bench,'save',lambda *a:None)
    monkeypatch.setattr(bench.os,'fchmod',lambda *a:None,raising=False)
    class Process:
        def poll(self): return 1
    calls=[]
    monkeypatch.setattr(bench.subprocess,'Popen',lambda *a,**kw:Process())
    monkeypatch.setattr(bench,'stop_owned',lambda process,unit:calls.append(unit))
    case=bench.matrix(slots=[2],contexts=[32768])[0]
    result=bench.execute_case(base,case,root,tmp_path,tmp_path/'case',8)
    assert not result['passed'] and 'Candidate exited' in result['error']
    assert len(calls)==1 and calls[0].startswith('p100-concurrency-')
