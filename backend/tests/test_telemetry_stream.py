import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from fastapi.testclient import TestClient

from lab.main import create_app
from lab.telemetry_stream import TelemetryHub, TelemetryUnavailable


def test_concurrent_rest_reads_share_sample_and_preserve_age():
    calls = []
    def sample():
        calls.append(1)
        time.sleep(.02)
        return {'mode': 'mock'}
    hub = TelemetryHub(sample)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            frames = list(pool.map(lambda _: hub.get(), range(16)))
        assert len(calls) == 1
        assert {f['sample_sequence'] for f in frames} == {1}
        assert hub.get()['sample_age_ms'] >= frames[0]['sample_age_ms']
    finally:
        hub.close()


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), object()])
def test_failed_sample_never_relabels_cached_success_as_fresh(bad):
    values = iter([{'mode': 'mock'}, {'invalid': bad}])
    hub = TelemetryHub(lambda: next(values), interval=.001)
    try:
        assert hub.get()['sample_sequence'] == 1
        hub.attempted = -float('inf')  # Deterministic across Windows clock resolution.
        with pytest.raises(TelemetryUnavailable):
            hub.get()
        assert hub.sequence == 2
    finally:
        hub.close()


def test_subscribers_share_latest_sample_capacity_and_idle_shutdown():
    async def exercise():
        calls = []
        hub = TelemetryHub(lambda: calls.append(1) or {'mode': 'mock'}, interval=.03, max_clients=2)
        try:
            a, first = hub.subscribe()
            b, slow = hub.subscribe()
            with pytest.raises(TelemetryUnavailable):
                hub.subscribe()
            await asyncio.wait_for(first.get(), 1)
            await asyncio.sleep(.15)
            assert first.qsize() == slow.qsize() == 1
            fast_frame = first.get_nowait()
            slow_frame = slow.get_nowait()
            assert fast_frame[0] == slow_frame[0] == len(calls)
            hub.unsubscribe(a)
            hub.unsubscribe(b)
            await asyncio.sleep(.08)
            count = len(calls)
            await asyncio.sleep(.08)
            assert len(calls) == count
        finally:
            hub.close()
        assert not hub.thread.is_alive()
    asyncio.run(exercise())


def test_stream_authentication_cannot_start_sampler(tmp_path):
    app = create_app({'token': 'test-secret-' * 4, 'db': tmp_path / 'lab.db', 'telemetry': 'mock'})
    with TestClient(app) as client:
        assert client.get('/api/system/stream').status_code == 401
        assert app.state.telemetry_hub.thread is None
        assert not app.state.telemetry_hub.clients
        response = client.get('/api/system', headers={'Authorization': 'Bearer ' + 'test-secret-' * 4})
        assert response.status_code == 200
        assert response.json()['telemetry_stream'] is True
        assert response.headers['cache-control'] == 'no-store'


def test_new_subscriber_does_not_replay_cached_sample_to_existing_reader():
    async def exercise():
        hub = TelemetryHub(lambda: {'mode': 'mock'}, interval=60)
        try:
            a, first = hub.subscribe()
            original = await asyncio.wait_for(first.get(), 1)
            b, second = hub.subscribe()
            assert await asyncio.wait_for(second.get(), 1) == original
            await asyncio.sleep(.02)
            assert first.empty()
            hub.unsubscribe(a)
            hub.unsubscribe(b)
        finally:
            hub.close()
    asyncio.run(exercise())


def test_stream_capacity_returns_retry_without_collecting(tmp_path):
    app = create_app({'token': 'test-secret-' * 4, 'db': tmp_path / 'lab.db', 'telemetry': 'mock'})
    app.state.telemetry_hub.max_clients = 0
    with TestClient(app) as client:
        response = client.get('/api/system/stream', headers={'Authorization': 'Bearer ' + 'test-secret-' * 4})
        assert response.status_code == 429
        assert response.headers['retry-after'] == '5'
        assert app.state.telemetry_hub.thread is None
