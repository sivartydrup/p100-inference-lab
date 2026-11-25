"""Offline matrix planning and explicitly authorized, private Scream experiments.

Never stops a pre-existing service. Run only during a dedicated test window.
Stdlib only; results/config/logs are private, outside the source tree.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import statistics
import subprocess
import threading
import time
import uuid

CONTEXTS = (32768, 65536, 131072, 262144, 524288)
SLOTS = (1, 2, 3, 4)
PORT = 18087
UNITS = ('kmic-qwen27b', 'p100-lab-kmic-moe', 'p100-lab-strata',
         'p100-lab-scream', 'p100-lab-scream-512k', 'p100-lab-inkling',
         'p100-lab-pxa', 'p100-lab-qr0n')


def matrix(slots=SLOTS, contexts=CONTEXTS, tier='screen'):
    """Context is requested per slot; observed context/slots must match at runtime."""
    return [dict(slots=s, context=c, tier=tier,
                 concurrency=[s] if tier == 'probe' else sorted({1, s, 2*s}),
                 target_prompt_tokens=min(8192, c-2048) if tier == 'screen' else c-2048,
                 output_tokens=1024 if tier == 'capacity' else 256,
                 repeats=3 if tier == 'capacity' else 1,
                 turns=2 if tier == 'capacity' else 1)
            for s in sorted(slots) for c in sorted(contexts)]


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def fixed(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=8).stdout.strip()


def hardware():
    rows = fixed('/usr/bin/nvidia-smi',
                 '--query-gpu=uuid,name,temperature.gpu,power.draw,power.limit,memory.used,memory.total,utilization.gpu',
                 '--format=csv,noheader,nounits').splitlines()
    gpus = []
    for row in rows:
        uuid, name, *values = [part.strip() for part in row.split(',')]
        values = [float(v) for v in values]
        if len(values) != 6 or not all(math.isfinite(v) for v in values):
            raise ValueError('Invalid GPU telemetry')
        gpus.append(dict(uuid=uuid, name=name, temperature_c=values[0], power_w=values[1],
                         limit_w=values[2], used_mib=values[3], total_mib=values[4], utilization=values[5]))
    mem = {line.split(':')[0]: int(line.split()[1])*1024
           for line in Path('/proc/meminfo').read_text().splitlines() if line.split()[1].isdigit()}
    return dict(at=time.time(), gpus=gpus, available_bytes=mem['MemAvailable'],
                total_bytes=mem['MemTotal'], swap_used_bytes=mem['SwapTotal']-mem['SwapFree'],
                load1=os.getloadavg()[0])


def safe_sample(sample, initial_swap, reserve_gib):
    cards = sample['gpus']
    if len(cards) != 2 or any(g['name'] != 'Tesla P100-PCIE-16GB' for g in cards):
        raise RuntimeError('This matrix requires exactly the reviewed two P100 16GB cards')
    if any(g['temperature_c'] >= 75 or abs(g['limit_w']-150) > .5 for g in cards):
        raise RuntimeError('75C cutoff or 150W policy check failed')
    if sample['available_bytes'] < reserve_gib * 2**30:
        raise RuntimeError('Host RAM reserve reached')
    if sample['swap_used_bytes'] > initial_swap + 256*2**20:
        raise RuntimeError('Swap growth exceeded 256MiB')


def idle_window():
    # Read only. The operator must stop agents/services; this tool cannot do it.
    for unit in UNITS:
        state = fixed('/usr/bin/systemctl', '--user', 'show', unit+'.service', '-p', 'ActiveState', '--value')
        if state not in ('inactive', 'failed'):
            raise RuntimeError('A managed engine is active; no service was stopped')
    processes = fixed('/usr/bin/nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits')
    if processes.strip():
        raise RuntimeError('GPU compute processes present; no process was stopped')
    snapshot = hardware()
    if any(g['temperature_c'] >= 60 or g['utilization'] >= 10 or g['used_mib'] >= 1024
           for g in snapshot['gpus']):
        raise RuntimeError('Both GPUs must be idle and below 60C')
    safe_sample(snapshot, snapshot['swap_used_bytes'], 6)
    with socket.socket() as listener:
        listener.settimeout(.5)
        if listener.connect_ex(('127.0.0.1', PORT)) == 0:
            raise RuntimeError('Candidate port already has a listener')
    with socket.socket() as probe:
        # TIME_WAIT from the previous candidate is not an active listener.
        # SO_REUSEADDR still refuses an existing listener on this address/port.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    return snapshot


def flag(args, name, value):
    if args.count(name) != 1:
        raise ValueError('Expected exactly one '+name)
    index = args.index(name)+1
    if index >= len(args):
        raise ValueError('Missing flag value')
    args[index] = str(value)


def profile_for(base, case, root, directory, key):
    cfg = copy.deepcopy(base)
    if Path(cfg['exe']).resolve() != (root/'build/split/strata-split-2').resolve():
        raise ValueError('Unexpected engine executable')
    if Path(cfg['cwd']).resolve() != (root/'engine').resolve():
        raise ValueError('Unexpected engine working directory')
    args = cfg['args']
    if '--slots' in args or '--batch-groups' in args:
        raise ValueError('Baseline has extra slot policy; review separately')
    flag(args, '--batch', case['slots'])
    flag(args, '--max-context', case['context'])
    # Preserve memory/expert/cache/MTP settings: do not confuse them with slot gains.
    env = cfg['env']
    if case['context'] > 262144:
        env.update(FNX_ROPE_SCALE='2', FNX_YARN_ORIG_CTX='262144')
    else:
        env.pop('FNX_ROPE_SCALE', None)
        env.pop('FNX_YARN_ORIG_CTX', None)
    cfg.update(model_name='p100-concurrency-test', port=PORT, api_key=key,
               log=str(directory/'engine.log'))
    return cfg


class API:
    def __init__(self, key, stopped):
        self.key, self.stopped = key, stopped
        self.connections = set()
        self.wires = {}
        self.lock = threading.Lock()

    def connection(self):
        if self.stopped.is_set():
            raise RuntimeError('Run aborted')
        connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=1800)
        with self.lock:
            self.connections.add(connection)
        return connection

    def connect(self, connection):
        connection.connect()
        with self.lock:
            self.wires[connection] = connection.sock
        if self.stopped.is_set():
            self.cancel()
            raise RuntimeError('Run aborted')

    def close(self, connection):
        connection.close()
        with self.lock:
            self.connections.discard(connection)
            self.wires.pop(connection, None)

    def cancel(self):
        self.stopped.set()
        with self.lock:
            for wire in self.wires.values():
                if wire:
                    try:
                        wire.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def json(self, path, payload=None):
        connection = self.connection()
        connection.timeout = 60  # Large count-only requests can be CPU-bound.
        try:
            self.connect(connection)
            connection.request('GET' if payload is None else 'POST', path,
                               body=None if payload is None else json.dumps(payload),
                               headers={'Authorization': 'Bearer '+self.key, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError('Candidate HTTP status '+str(response.status))
            return json.loads(response.read(16*1024*1024))
        finally:
            self.close(connection)

    def generate(self, messages, output_tokens, barrier, deadline):
        connection = self.connection()
        try:
            barrier.wait(timeout=30)
            started = time.monotonic()
            connection.timeout = max(1, min(1800, deadline-started))
            self.connect(connection)
            payload = dict(model='p100-concurrency-test', messages=messages, stream=True,
                           stream_options={'include_usage': True}, max_tokens=output_tokens,
                           temperature=0, seed=42, chat_template_kwargs={'enable_thinking': False})
            connection.request('POST', '/v1/chat/completions', json.dumps(payload),
                               {'Authorization': 'Bearer '+self.key, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError('Generation HTTP status '+str(response.status))
            first = content_first = last = None
            text, usage, finish, done = '', None, None, False
            while True:
                if self.stopped.is_set() or time.monotonic() > deadline:
                    raise RuntimeError('Generation cancelled or deadline reached')
                wire = self.wires.get(connection)
                if wire:
                    wire.settimeout(max(1, deadline-time.monotonic()))
                line = response.readline(2*1024*1024)
                if not line:
                    break
                if not line.startswith(b'data:'):
                    continue
                raw = line[5:].strip()
                if raw == b'[DONE]':
                    done = True
                    break
                event = json.loads(raw)
                if event.get('error'):
                    raise RuntimeError('Candidate emitted a stream error')
                usage = event.get('usage') or usage
                for choice in event.get('choices', []):
                    delta = choice.get('delta', {})
                    now = time.monotonic()
                    if delta.get('content') or delta.get('reasoning_content'):
                        first = first if first is not None else now
                        last = now
                    if delta.get('content'):
                        content_first = content_first if content_first is not None else now
                        text += delta['content']
                    finish = choice.get('finish_reason') or finish
            ended = time.monotonic()
            if not done or first is None or not usage or not finish:
                raise RuntimeError('Incomplete stream, missing token usage or no generated token')
            n = usage.get('completion_tokens')
            if type(n) is not int or n <= 0 or type(usage.get('prompt_tokens')) is not int:
                raise RuntimeError('Invalid server token counts')
            return dict(started=started, ended=ended, ttft_s=first-started,
                        first_generated_at=first, last_generated_at=last,
                        content_ttft_s=None if content_first is None else content_first-started,
                        wall_s=ended-started, completion_tokens=n, prompt_tokens=usage['prompt_tokens'],
                        decode_tokens_s=(n-1)/(last-first) if n>1 and last>first else None,
                        finish_reason=finish, output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        text=text)
        finally:
            self.close(connection)


def prompt(api, target, marker):
    # Unique early prefix prevents the agents from accidentally sharing one cached prompt.
    prefix = f'Independent coding session {marker}.\n'
    suffix = ('\nTask: output only SESSION='+marker+' on the first line. Then write a detailed '
              'Python implementation of a bounded job scheduler with cancellation, fairness and tests. '
              'Do not stop after the marker. Use the following synthetic repository as background.\n')
    low, high, best = 0, max(1, target//8), None
    for _ in range(22):
        middle = (low+high)//2
        text = prefix + suffix + ''.join(
            f'def queue_item_{i}(index: int) -> int:\n    return (index * {17+i%97} + {3+i}) % 65521\n'
            for i in range(middle))
        messages = [{'role': 'user', 'content': text}]
        count = api.json('/v1/messages/count_tokens', dict(model='p100-concurrency-test',
                         max_tokens=1024, thinking={'type': 'disabled'}, messages=messages))['input_tokens']
        if type(count) is not int or count < 1:
            raise ValueError('Invalid prompt token count')
        if count <= target:
            best = (messages, count)
            low = middle+1
        else:
            high = middle-1
        if low > high:
            break
    if best is None or best[1] < target*.98:
        raise RuntimeError('Could not construct a prompt within 2% of target')
    return best


def wave(api, count, case, trial, directory, stopped):
    target = case['target_prompt_tokens']
    # Leave space for the first response and follow-up inside the same context.
    if case['turns'] > 1:
        target -= case['output_tokens']+256
    markers = [secrets.token_hex(8) for _ in range(count)]
    prepared = [prompt(api, target, marker) for marker in markers]
    results = []
    for turn in range(case['turns']):
        barrier = threading.Barrier(count)
        deadline = time.monotonic()+1800
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(api.generate, messages, case['output_tokens'], barrier, deadline)
                       for messages, _ in prepared]
            try:
                rows = [None]*count
                indexes = {future:i for i,future in enumerate(futures)}
                for future in as_completed(futures):
                    rows[indexes[future]] = future.result()
            except (Exception, KeyboardInterrupt):
                api.cancel()
                raise
        for i, row in enumerate(rows):
            # Count endpoint uses Anthropic conversion; independently validate actual chat usage.
            if turn == 0 and abs(row['prompt_tokens']-prepared[i][1]) > 128:
                raise RuntimeError('Chat prompt count differs from calibrated count by more than 128')
            if row['prompt_tokens']+case['output_tokens'] > case['context']:
                raise RuntimeError('Actual prompt/output budget exceeds observed context')
            row['marker_ok'] = 'SESSION='+markers[i] in row['text']
            if not row['marker_ok']:
                raise RuntimeError('Session marker correctness check failed')
            # Follow-up preserves this agent's own exact history, never another agent's.
            prepared[i][0].extend([{'role': 'assistant', 'content': row['text']},
                                  {'role': 'user', 'content': 'Again begin with SESSION='+markers[i]+
                                   '. Improve cancellation and add a regression test.'}])
            row.pop('text')
            row.update(client=i, turn=turn, trial=trial, concurrency=count,
                       requested_output_tokens=case['output_tokens'])
            results.append(row)
        save(directory/f'wave-{count}-{trial}-{turn}.json', rows)
        if stopped.is_set():
            raise RuntimeError('Run aborted')
    return results


def stop_owned(process, unit):
    # Stop only this invocation's transient cgroup, including engine descendants.
    if not unit.startswith('p100-concurrency-') or not unit.endswith('.service'):
        raise ValueError('Invalid experiment unit')
    subprocess.run(['/usr/bin/systemctl', '--user', 'stop', unit],
                   capture_output=True, timeout=25, check=False)
    if process is not None:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()  # The systemd-run client, never a pre-existing engine.
            process.wait(timeout=10)


def verify_cgroup(unit, memory_max):
    pid = int(fixed('/usr/bin/systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'))
    entries = Path(f'/proc/{pid}/cgroup').read_text().splitlines()
    location = next(line[3:] for line in entries if line.startswith('0::'))
    if unit not in location.split('/'):
        raise RuntimeError('Candidate PID is outside its transient unit')
    group = (Path('/sys/fs/cgroup')/location.lstrip('/')).resolve()
    if not group.is_relative_to(Path('/sys/fs/cgroup')):
        raise RuntimeError('Unexpected cgroup location')
    if int((group/'memory.max').read_text()) != memory_max or (group/'memory.swap.max').read_text().strip() != '0':
        raise RuntimeError('Effective candidate memory/swap limits not enforced')


def execute_case(base, case, root, data, directory, reserve):
    initial = idle_window()
    directory.mkdir(mode=0o700)
    key = secrets.token_hex(32)
    cfg = profile_for(base, case, root, directory, key)
    save(directory/'profile.private.json', cfg)
    save(directory/'case.json', case)
    save(directory/'initial.json', initial)
    stopped, monitor_done = threading.Event(), threading.Event()
    failure = []
    process = None
    unit = 'p100-concurrency-'+uuid.uuid4().hex+'.service'
    memory_max = initial['available_bytes']-reserve*2**30
    if memory_max < 16*2**30:
        raise RuntimeError('Insufficient memory for an isolated candidate')
    rows = []
    result = dict(case=case, passed=False, memory_limit_bytes=memory_max, transient_unit=unit)

    def monitor():
        nonlocal process
        with (directory/'telemetry.jsonl').open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            while not monitor_done.is_set():
                try:
                    sample = hardware()
                    stream.write(json.dumps(sample)+'\n')
                    stream.flush()
                    safe_sample(sample, initial['swap_used_bytes'], reserve)
                except Exception as error:
                    failure.append(type(error).__name__+': '+str(error))
                    stopped.set()
                    # Terminate only the candidate group, including when HTTP is blocked.
                    if process is not None:
                        stop_owned(process, unit)
                    return
                monitor_done.wait(1)

    thread = threading.Thread(target=monitor, daemon=True)
    try:
        with (directory/'server.private.log').open('x') as log:
            os.fchmod(log.fileno(), 0o600)
            process = subprocess.Popen(['/usr/bin/systemd-run', '--user', '--quiet', '--collect',
                                        '--wait', '--pipe', '--service-type=exec', '--unit='+unit,
                                        '--property=MemoryMax='+str(memory_max), '--property=MemorySwapMax=0',
                                        '--property=RuntimeMaxSec=14400', '--property=KillMode=control-group',
                                        '--property=TimeoutStopSec=15', '--property=Restart=no',
                                        '/usr/bin/env', '-i', 'HOME='+str(Path.home()),
                                        'PATH=/usr/bin:/bin', 'LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64',
                                        '/bin/bash', str(root/'run.sh'), str(data), '--config',
                                        str(directory/'profile.private.json'), '--host', '127.0.0.1',
                                        '--port', str(PORT)], cwd=root, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            thread.start()
            for attempt in range(50):
                try:
                    verify_cgroup(unit, memory_max)
                    break
                except (subprocess.CalledProcessError, FileNotFoundError, ValueError, StopIteration):
                    if attempt == 49:
                        raise RuntimeError('Candidate cgroup did not become verifiable') from None
                    time.sleep(.2)
            api = API(key, stopped)
            start = time.monotonic()
            while time.monotonic()-start < 900:
                if stopped.is_set() or process.poll() is not None:
                    raise RuntimeError('Candidate exited or resource guard aborted startup')
                try:
                    health = api.json('/health')
                    if health.get('loaded') is True:
                        if health.get('api_key') is not True:
                            raise RuntimeError('Candidate authentication is not enabled')
                        break
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Startup exceeded 900 seconds')
            props = api.json('/props')
            verify_cgroup(unit, memory_max)
            result['observed'] = dict(slots=props.get('total_slots'),
                context=props.get('default_generation_settings', {}).get('n_ctx'),
                alias=props.get('model_alias'), startup_s=time.monotonic()-start)
            if result['observed']['slots'] != case['slots'] or result['observed']['context'] != case['context']:
                raise RuntimeError('Engine fell back from requested slots/context')
            if props.get('model_alias') != 'p100-concurrency-test' or props.get('is_sleeping'):
                raise RuntimeError('Unexpected candidate identity/state')
            # Unmeasured warmup; no synthetic result is used as a measured sample.
            warmup = api.generate([{'role':'user','content':'Say ready.'}], 32, threading.Barrier(1), time.monotonic()+120)
            warmup.pop('text')
            save(directory/'warmup.json', warmup)
            for count in case['concurrency']:
                for trial in range(case['repeats']):
                    if stopped.is_set():
                        raise RuntimeError('Resource guard aborted run')
                    rows.extend(wave(api, count, case, trial, directory, stopped))
            result.update(passed=not stopped.is_set(), requests=rows)
    except (Exception, KeyboardInterrupt) as error:
        result.update(error=type(error).__name__+': '+str(error), requests=rows)
        if isinstance(error, KeyboardInterrupt):
            failure.append('Operator interruption')
    finally:
        stop_owned(process, unit)
        monitor_done.set()
        if thread.ident:
            thread.join(timeout=12)
        result['guard_errors'] = failure
        samples = [json.loads(line) for line in (directory/'telemetry.jsonl').read_text().splitlines()] if thread.ident else []
        if samples:
            result['resources'] = dict(min_available_gib=min(s['available_bytes'] for s in samples)/2**30,
                max_swap_growth_mib=max(s['swap_used_bytes']-initial['swap_used_bytes'] for s in samples)/2**20,
                peak_temperature_c=max(g['temperature_c'] for s in samples for g in s['gpus']),
                peak_total_gpu_power_w=max(sum(g['power_w'] for g in s['gpus']) for s in samples),
                peak_used_vram_mib_by_uuid={g['uuid']:max(card['used_mib'] for s in samples for card in s['gpus'] if card['uuid']==g['uuid']) for g in initial['gpus']})
        result['passed'] = result['passed'] and not failure
        save(directory/'result.json', result)
    return result


def summarize(results):
    summaries = []
    for result in results:
        for concurrency, kind in [(c,t) for c in result['case']['concurrency'] for t in range(result['case']['turns'])]:
            rows = [r for r in result.get('requests', []) if r['concurrency']==concurrency and r['turn']==kind]
            if not rows:
                continue
            # Each synchronized wave's makespan includes prefill, queueing and decode.
            rates = []
            for trial, turn in sorted({(r['trial'],r['turn']) for r in rows}):
                group = [r for r in rows if (r['trial'],r['turn'])==(trial,turn)]
                rates.append(sum(r['completion_tokens'] for r in group)/
                             (max(r['ended'] for r in group)-min(r['started'] for r in group)))
            latencies = sorted(r['ttft_s'] for r in rows)
            events = sorted([(r['first_generated_at'],1) for r in rows]+
                            [(r['last_generated_at'],-1) for r in rows])
            active = peak = 0
            for _, change in events:
                active += change
                peak = max(peak, active)
            summaries.append(dict(slots=result['case']['slots'], context=result['case']['context'],
                concurrency=concurrency, turn=kind, tier=result['case']['tier'], passed=result['passed'],
                requests=len(rows), aggregate_output_tokens_s_median=statistics.median(rates),
                ttft_s_median=statistics.median(latencies),
                ttft_s_p95=latencies[max(0, math.ceil(.95*len(latencies))-1)],
                max_wall_s=max(r['wall_s'] for r in rows),
                peak_overlapping_generated_streams=peak,
                output_budget_fraction_median=statistics.median(r['completion_tokens']/r['requested_output_tokens'] for r in rows),
                resources=result.get('resources'),
                notice='End-to-end throughput includes prefill/queueing; decode rate is based on streamed token counts, not per-token timestamps.'))
    return summaries


def recommendations(summary):
    """Compare like-for-like capacity workloads, without declaring a universal winner."""
    eligible = [r for r in summary if r['passed'] and r['tier']=='capacity'
                and r['concurrency']==r['slots'] and r['requests']>=3*r['slots']
                and r['output_budget_fraction_median']>=.9]
    choices = []
    for context, turn in sorted({(r['context'],r['turn']) for r in eligible}):
        rows = [r for r in eligible if (r['context'],r['turn'])==(context,turn)]
        choices.append(dict(context=context,turn=turn,
            best_aggregate=min(rows,key=lambda r:-r['aggregate_output_tokens_s_median']),
            best_first_token_latency=min(rows,key=lambda r:r['ttft_s_p95'])))
    return dict(comparisons=choices, notice='Candidates, not production recommendations. '
                'Require representative coding/tool correctness and a sustained finalist run. '
                'Screening results and short early-EOS outputs are excluded.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--window-confirmed', action='store_true')
    parser.add_argument('--slots', type=int, nargs='+', choices=SLOTS, default=list(SLOTS))
    parser.add_argument('--contexts', type=int, nargs='+', choices=CONTEXTS, default=list(CONTEXTS))
    parser.add_argument('--tier', choices=('screen','probe','capacity'), default='screen')
    parser.add_argument('--output-tokens', type=int, choices=(256,1024,8192))
    parser.add_argument('--clients', type=int, nargs='+', choices=range(1,9),
                        help='Override client counts for matched-load comparisons')
    parser.add_argument('--prompt-tokens', type=int,
                        help='Target initial input length instead of near-limit input')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--base-profile', type=Path)
    parser.add_argument('--reserve-gib', type=int, choices=range(4,33), default=8)
    args = parser.parse_args()
    cases = matrix(set(args.slots), set(args.contexts), args.tier)
    if args.output_tokens:
        for case in cases:
            case['output_tokens'] = args.output_tokens
            case['target_prompt_tokens'] = min(case['target_prompt_tokens'], case['context']-args.output_tokens-1024)
    if args.clients:
        for case in cases:
            case['concurrency'] = sorted(set(args.clients))
    if args.prompt_tokens is not None:
        if not 512 <= args.prompt_tokens <= 524288:
            parser.error('Prompt target must be 512..524288 tokens')
        for case in cases:
            reserved = case['output_tokens']+256 if case['turns']>1 else 0
            budget = args.prompt_tokens+reserved
            if budget+case['output_tokens']+1024 > case['context']:
                parser.error('Prompt, follow-up and output reserves do not fit the requested context')
            case['target_prompt_tokens'] = budget
            case['initial_prompt_override'] = args.prompt_tokens
    if not args.execute:
        print(json.dumps(dict(execution_enabled=False, cases=cases,
                              notice='Offline plan only. No network, subprocess or hardware access.'), indent=2))
        return
    if not args.window_confirmed or not args.output or not args.base_profile:
        parser.error('Execution needs --window-confirmed, --output and --base-profile')
    if os.name != 'posix' or os.geteuid()==0:
        parser.error('Execute as the non-root Ubuntu engine owner')
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    import fcntl  # Linux execution only; offline planning remains portable.
    lock_path = Path('/run/user')/str(os.getuid())/'p100-concurrency.lock'
    descriptor = os.open(lock_path, os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Unsafe experiment lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
        return run_matrix(args, cases)
    finally:
        os.close(descriptor)


def run_matrix(args, cases):
    root = Path.home()/'inference/scream-p100'
    data = Path.home()/'inference/scream-flash-next'
    base = json.loads(args.base_profile.read_text())
    idle_window()  # Before creating outputs or starting anything.
    directory = args.output.resolve()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    save(directory/'plan.json', cases)
    # Hash private input without copying its existing API key into evidence.
    save(directory/'provenance.json', dict(base_profile_sha256=hashlib.sha256(args.base_profile.read_bytes()).hexdigest(),
         engine_sha256=hashlib.sha256(Path(base['exe']).read_bytes()).hexdigest(),
         scream_revision=fixed('/usr/bin/git','-C',str(root),'rev-parse','HEAD'),
         reserve_gib=args.reserve_gib, cutoff_c=75, power_limit_w=150))
    results = []
    blocked_slots = set()
    for index, case in enumerate(cases):
        if case['slots'] in blocked_slots:
            continue
        print(f"Case {index+1}/{len(cases)}: {case['slots']} slots, {case['context']} context", flush=True)
        try:
            result = execute_case(base, case, root, data, directory/f'case-{index:03}', args.reserve_gib)
        except (Exception, KeyboardInterrupt) as error:
            result = dict(case=case, passed=False, requests=[],
                          error=type(error).__name__+': '+str(error),
                          guard_errors=['Preflight or cleanup failure; review before continuing'])
            save(directory/f'abort-{index:03}.json', result)
        results.append(result)
        print('passed' if result['passed'] else 'failed; matrix stopped for review', flush=True)
        if not result['passed']:
            if result.get('guard_errors'):
                break  # A thermal/RAM/policy guard abort ends the entire experiment.
            blocked_slots.add(case['slots'])  # Skip larger contexts at this slot count.
    save(directory/'summary.json', summarize(results))
    save(directory/'candidates.json', recommendations(summarize(results)))
    print('Private evidence: '+str(directory))
    print('No existing service was stopped, restarted or reconfigured. No production engine was restored.')
    return 0 if results and all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
