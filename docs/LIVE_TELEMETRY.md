# Shared live telemetry

The browser requests authenticated `GET /api/system/stream` using Fetch and its
existing Bearer header. Tokens remain in memory and never enter URLs or browser
storage. No new management endpoint or host permission is introduced.

One lazy sampler per API process serves all readers, including `GET /api/system`.
The supported production command uses one worker. Sampling starts at most once
per second and stops between REST requests when there are no stream subscribers.
Slow NVIDIA calls reduce the actual update rate; this is near-live monitoring,
not a hard real-time guarantee. Each sample performs the existing read-only
system collection and one NVIDIA query for all cards. Inventory, jobs and history
keep their existing five-second polling schedule.

Each process permits eight streams. Additional clients receive 429 with
`Retry-After: 5`; browser polling remains available. A client has one queued latest
sample and at most one scheduled delivery callback, so slow readers cannot grow
an unbounded history. SSE responses disable caching and request proxy buffering
be disabled. An added reverse proxy must honor those settings.

Frames include a sequence and monotonic sample age. The browser rejects frames
older than three seconds, repeated sequences and oversized frames. Ten seconds
without a valid sample aborts the stream; heartbeat comments do not refresh data
age. It reconnects after two seconds while retaining five-second REST fallback.
Hidden tabs close their stream and reconnect when visible. Existing fifteen-second
stale warnings remain; cached REST responses retain their original sample age.
Disconnect aborts requests and clears the token and displayed readings.

This stream is separate from the benchmark worker's fresh safety readings. It
cannot grant execution permission, change GPU caps or replace thermal checks.
No dependencies or license terms change.

## Validation and deployment boundary

Automated tests cover shared concurrent REST reads, bounded slow subscribers,
idle collection, shutdown, authentication, connection capacity, sampling failure,
non-finite data, browser fallback/disconnect and stale-frame rejection. Container
acceptance opens two real HTTP streams and checks matching sample sequences.

Server CPU cost and inference impact have not yet been measured. After review,
measure a candidate on Ubuntu without replacing the running dashboard: compare
idle baseline against one and several subscribers, record API/NVIDIA query rate
and CPU usage, then assess a separately approved inference comparison if needed.
Production deployment is a separate action. Existing services and GPU policies
remain unchanged by this branch.
