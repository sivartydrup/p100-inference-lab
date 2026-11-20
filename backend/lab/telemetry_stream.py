"""One bounded, lazy sampler per API process, shared by authenticated readers."""
import asyncio
import json
import math
import threading
import time


class TelemetryUnavailable(RuntimeError):
    pass


class TelemetryHub:
    def __init__(self, sample, *, interval=1.0, max_clients=8):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Invalid telemetry interval')
        self.sample = sample
        self.interval = interval
        self.max_clients = max_clients
        self.lock = threading.Lock()
        self.sampling = threading.Lock()
        self.changed = threading.Event()
        self.thread = None
        self.closed = False
        self.clients = {}
        self.sequence = 0
        self.value = None
        self.created = 0.0
        self.attempted = -math.inf
        self.failed = False

    def frame(self, event):
        sequence, value, created, failed = event
        if failed or value is None:
            raise TelemetryUnavailable('Telemetry temporarily unavailable')
        return {**value, 'telemetry_stream': True, 'sample_sequence': sequence,
                'sample_age_ms': max(0, (time.monotonic()-created)*1000)}

    def _event(self):
        return self.sequence, self.value, self.created, self.failed

    def _deliver(self, key):
        with self.lock:
            client = self.clients.get(key)
            if client is None:
                return
            client['pending'] = False
            event = self._event()
            if client['last_sequence'] == event[0]:
                return
            client['last_sequence'] = event[0]
        queue = client['queue']
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    def _publish(self):
        # At most one queued loop callback and one queued sample per subscriber.
        for key, client in list(self.clients.items()):
            if client['pending']:
                continue
            client['pending'] = True
            try:
                client['loop'].call_soon_threadsafe(self._deliver, key)
            except RuntimeError:
                self.clients.pop(key, None)

    def refresh(self):
        with self.sampling:
            with self.lock:
                if self.closed:
                    raise TelemetryUnavailable('Telemetry closed')
                if time.monotonic()-self.attempted < self.interval:
                    return
                self.attempted = time.monotonic()
            try:
                value = self.sample()
                json.dumps(value, allow_nan=False)  # Refuse non-finite or malformed samples.
                failed = False
            except Exception:
                value, failed = None, True
            with self.lock:
                self.value, self.failed = value, failed
                self.created = time.monotonic()
                self.sequence += 1
                self._publish()

    def get(self):
        self.refresh()
        with self.lock:
            event = self._event()
        return self.frame(event)

    def subscribe(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        key = object()
        with self.lock:
            if self.closed or len(self.clients) >= self.max_clients:
                raise TelemetryUnavailable('Telemetry stream capacity reached')
            self.clients[key] = {'loop': loop, 'queue': queue, 'pending': False, 'last_sequence': 0}
            if self.sequence:
                self._publish()
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name='lab-telemetry')
                self.thread.start()
        self.changed.set()
        return key, queue

    def unsubscribe(self, key):
        with self.lock:
            self.clients.pop(key, None)
        self.changed.set()

    def _run(self):
        while True:
            with self.lock:
                if self.closed:
                    return
                active = bool(self.clients)
                delay = max(.01, self.interval-(time.monotonic()-self.attempted))
            if active:
                try:
                    self.refresh()
                except TelemetryUnavailable:
                    return
            self.changed.wait(delay if active else None)
            self.changed.clear()

    def close(self):
        with self.lock:
            self.closed = True
        self.changed.set()
        if self.thread:
            self.thread.join(timeout=6)
