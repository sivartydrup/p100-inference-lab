"""Synthetic session metadata only; no GPU access or inference execution."""
import hashlib
import json

import pytest
from pydantic import ValidationError

from lab import native_store as store
from lab.jobs import encode
from lab.measured_history import MeasuredRun, import_run
from lab.native_cli import main
from lab.native_config import NativeError
from resident_fixture import measurement
from test_measured_history import write_bundle

GPU = measurement()['gpu_uuid']
PEER = 'GPU-22222222-2222-2222-2222-222222222222'
SESSION = '33333333-3333-4333-8333-333333333333'


def parsed(data):
    return MeasuredRun.model_validate_json(json.dumps(data))


def context():
    return {'session_id': SESSION, 'gpu_uuids': [GPU, PEER], 'enclosure': 'closed'}


def test_legacy_digest_stays_byte_compatible_and_reimports(tmp_path):
    run = parsed(measurement())
    legacy = run.model_dump(mode='json', exclude={'test_context'})
    expected = hashlib.sha256(encode(legacy).encode()).hexdigest()
    assert run.digest() == expected
    db = tmp_path / 'native.sqlite3'; store.initialize(db)
    import_run(db, run)
    with store.connect(db) as connection:
        connection.execute('UPDATE native_jobs SET preset_hash=?', (expected,))
    assert import_run(db, run)['id'] == str(run.run_id)
    assert 'test_context' not in run.provenance()


def test_session_metadata_is_immutable_and_keeps_per_gpu_comparisons(tmp_path):
    data = measurement(); data['test_context'] = context()
    first = parsed(data)
    db = tmp_path / 'native.sqlite3'; store.initialize(db)
    job = import_run(db, first)
    assert job['provenance']['test_context'] == context()
    assert 'Operator-declared' in job['provenance']['context_assurance']
    assert store.claim(db) is None
    data['test_context']['gpu_uuids'].reverse()
    assert parsed(data).digest() == first.digest()
    data['test_context']['enclosure'] = 'open'
    with pytest.raises(NativeError, match='idempotency_conflict'):
        import_run(db, parsed(data))
    assert parsed(data).provenance()['comparison_key'] == first.provenance()['comparison_key']
    data['gpu_uuid'] = PEER
    peer = parsed(data)
    assert peer.provenance()['session_workload_key'] == first.provenance()['session_workload_key']
    assert peer.provenance()['comparison_key'] != first.provenance()['comparison_key']
    data['workload']['context'] = 4096
    assert parsed(data).provenance()['session_workload_key'] != first.provenance()['session_workload_key']


@pytest.mark.parametrize('change', [
    lambda c: c.update(gpu_uuids=[]),
    lambda c: c.update(gpu_uuids=[GPU, GPU]),
    lambda c: c.update(gpu_uuids=[PEER]),
    lambda c: c.update(session_id=None),
    lambda c: c.update(session_id='not-a-uuid'),
    lambda c: c.update(enclosure='probably fine'),
    lambda c: c.update(gpu_uuids=['../../secret']),
    lambda c: c.update(command='reboot'),
])
def test_invalid_operator_context_is_rejected(change):
    data = measurement(); data['test_context'] = context(); change(data['test_context'])
    with pytest.raises(ValidationError): parsed(data)


def test_cli_prepare_annotates_without_changing_source_id_or_executing(tmp_path):
    bundle = tmp_path / 'bundle'; bundle.mkdir(); write_bundle(bundle)
    output = tmp_path / 'prepared.json'
    args = ['--db', str(tmp_path / 'unused.sqlite3'), 'prepare-import', '--bundle', str(bundle),
            '--engine', 'kmic', '--model-id', 'synthetic-model', '--model-license', 'Synthetic only',
            '--output', str(output)]
    main(args + ['--session-id', SESSION, '--session-gpu', GPU, '--session-gpu', PEER, '--enclosure', 'closed'])
    run = parsed(json.loads(output.read_text()))
    assert run.provenance()['test_context'] == context()
    assert not (tmp_path / 'unused.sqlite3').exists()
    original = tmp_path / 'plain.json'; args[-1] = str(original)
    main(args)
    assert parsed(json.loads(original.read_text())).run_id == run.run_id
