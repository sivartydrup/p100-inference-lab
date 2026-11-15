import { useEffect, useRef, useState } from "react";

export type Job = {
  id: string;
  status: string;
  source: "fixture";
  created_at: string;
  cancel_requested: boolean;
  request: { preset: string; trials: number; warmup: number };
  error_code: string | null;
  summary: {
    sample_count: number;
    metrics: Record<string, { median: number; stddev: number }>;
  } | null;
};
export type Presets = {
  items: { id: string; label: string }[];
  submission_enabled: boolean;
};
type Detail = Job & {
  samples: {
    phase: string;
    trial: number;
    ttft_ms: number;
    prefill_tokens_per_second: number;
    decode_tokens_per_second: number;
  }[];
  events: { created_at: string; event: string }[];
};

function requestKey() {
  // getRandomValues is available on the trusted-LAN HTTP deployment too.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function BenchmarkLab({
  token,
  jobs,
  presets,
  stale,
  onChange,
}: {
  token: string;
  jobs: Job[];
  presets: Presets;
  stale: boolean;
  onChange: () => void;
}) {
  const [jobToken, setJobToken] = useState("");
  const [preset, setPreset] = useState("fixture-v1");
  const [trials, setTrials] = useState(5);
  const [warmup, setWarmup] = useState(1);
  const [timeout, setTimeoutSeconds] = useState(30);
  const [pending, setPending] = useState<{ key: string; body: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailError, setDetailError] = useState("");
  const mounted = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controllers.current.forEach((c) => c.abort());
    };
  }, []);

  async function request(
    path: string,
    init: RequestInit = {},
    privileged = false,
  ) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    init.signal?.addEventListener("abort", abort, { once: true });
    if (init.signal?.aborted) controller.abort();
    controllers.current.add(controller);
    const started = Date.now();
    const deadline = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch("/api/" + path, {
        ...init,
        signal: controller.signal,
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
          ...(privileged ? { "X-Lab-Job-Token": jobToken } : {}),
          ...init.headers,
        },
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(
          response.status === 403
            ? "Job permission denied. Check the separate job token and server settings."
            : `Request failed (${response.status}).`,
        );
      const value = await response.json();
      if (Date.now() - started >= 10_000)
        throw new Error("Request expired. Refresh before retrying.");
      return value;
    } catch (e) {
      if (controller.signal.aborted)
        throw new Error(
          privileged && path === "benchmark-jobs" && init.method === "POST"
            ? "Request timed out or was cancelled. A submission may already be queued; retry uses the same request ID."
            : "Request timed out or was cancelled.",
        );
      throw e;
    } finally {
      window.clearTimeout(deadline);
      controllers.current.delete(controller);
      init.signal?.removeEventListener("abort", abort);
    }
  }

  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    let activeRequest: AbortController | undefined;
    // Details have their own non-overlapping lifecycle, independent of list polling.
    setDetail(null);
    setDetailError("");
    const refresh = async () => {
      const controller = new AbortController();
      activeRequest = controller;
      try {
        const value = await request("benchmark-jobs/" + selected, {
          signal: controller.signal,
        });
        if (!disposed) {
          setDetail(value);
          setDetailError("");
        }
      } catch (e) {
        if (!disposed)
          setDetailError((e as Error).message + " Retrying automatically.");
      } finally {
        if (!disposed) timer = window.setTimeout(refresh, 5_000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      activeRequest?.abort();
    };
  }, [selected, token]);

  async function submit() {
    const submission = pending || {
      key: requestKey(),
      body: JSON.stringify({
        preset,
        trials,
        warmup,
        timeout_seconds: timeout,
      }),
    };
    setPending(submission);
    setBusy(true);
    setError("");
    try {
      const value = await request(
        "benchmark-jobs",
        {
          method: "POST",
          body: submission.body,
          headers: { "Idempotency-Key": submission.key },
        },
        true,
      );
      if (mounted.current) {
        setPending(null);
        setSelected(value.job.id);
        onChange();
      }
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function cancel(id: string) {
    setBusy(true);
    setError("");
    try {
      await request(`benchmark-jobs/${id}/cancel`, { method: "POST" }, true);
      if (mounted.current) onChange();
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function download() {
    if (!selected) return;
    setError("");
    try {
      const value = await request(`benchmark-jobs/${selected}/export`);
      if (!mounted.current) return;
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(value, null, 2)], {
          type: "application/json",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `fixture-job-${selected}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    }
  }

  return (
    <section id="fixture-lab">
      <div className="section-title">
        <h2>Fixture benchmark lab</h2>
        <span className="badge">SIMULATED / NO INFERENCE</span>
      </div>
      <p className="muted">
        Test the benchmark workflow without loading a model or using a GPU.
        Synthetic results stay separate from measured history.
      </p>
      {presets.submission_enabled ? (
        <form
          className="fixture-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label>
            Benchmark job token
            <input
              type="password"
              autoComplete="off"
              value={jobToken}
              onChange={(e) => setJobToken(e.target.value)}
              minLength={32}
              required
            />
          </label>
          <p className="muted">
            Separate permission to submit and cancel fixtures. Kept in this tab
            only. Queued jobs wait for the fixture worker.
          </p>
          <fieldset disabled={busy || !!pending}>
            <label>
              Fixture preset
              <select
                value={preset}
                onChange={(e) => setPreset(e.target.value)}
              >
                {presets.items.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Measured trials
              <input
                type="number"
                value={trials}
                min={1}
                max={20}
                required
                onChange={(e) => setTrials(Number(e.target.value))}
              />
            </label>
            <label>
              Warmup trials
              <input
                type="number"
                value={warmup}
                min={0}
                max={5}
                required
                onChange={(e) => setWarmup(Number(e.target.value))}
              />
            </label>
            <label>
              Job time limit (seconds)
              <input
                type="number"
                value={timeout}
                min={0.1}
                max={60}
                step={0.1}
                required
                onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
              />
            </label>
          </fieldset>
          <button disabled={busy || jobToken.length < 32}>
            {busy
              ? "Submitting..."
              : pending
                ? "Retry fixture submission"
                : "Queue simulated job"}
          </button>
          {pending && !busy && (
            <p className="muted">
              Retry preserves the same request and settings to avoid duplicates.
              Check the job list before starting another submission.
            </p>
          )}
        </form>
      ) : (
        <p className="empty">
          Fixture submissions are disabled by the administrator. History remains
          read-only.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {stale && (
        <p className="muted">
          Job statuses may be stale until the dashboard reconnects.
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Queued</th>
              <th>Fixture job</th>
              <th>Status</th>
              <th>Trials</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{new Date(j.created_at).toLocaleString()}</td>
                <td>
                  <code>{j.id.slice(0, 8)}</code>
                  <br />
                  {j.request.preset}
                  <br />
                  <small>SIMULATED</small>
                </td>
                <td>
                  {j.status}
                  {j.cancel_requested && j.status === "running"
                    ? " (cancellation requested)"
                    : ""}
                  {j.error_code && <p>{j.error_code}</p>}
                </td>
                <td>{j.request.trials}</td>
                <td>
                  <button
                    className="secondary"
                    type="button"
                    onClick={() => setSelected(j.id)}
                  >
                    View fixture results
                  </button>
                  {["queued", "running"].includes(j.status) && (
                    <button
                      className="secondary"
                      type="button"
                      disabled={
                        busy ||
                        stale ||
                        !presets.submission_enabled ||
                        jobToken.length < 32 ||
                        j.cancel_requested
                      }
                      onClick={() => void cancel(j.id)}
                    >
                      Cancel fixture
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!jobs.length && <p className="empty">No simulated jobs yet.</p>}
      {selected && (
        <article className="fixture-detail">
          <div className="section-title">
            <h3>Simulated job details</h3>
            <button
              className="secondary"
              type="button"
              onClick={() => void download()}
            >
              Export fixture JSON
            </button>
          </div>
          {detailError && (
            <p className="error" role="alert">
              {detailError}{" "}
              {detail &&
                "Showing the last successful details; they may be stale."}
            </p>
          )}
          {!detail && !detailError && <p>Loading job details...</p>}
          {detail && (
            <>
              <p>
                {detail.status} · fixture data only; no hardware performance was
                measured.
              </p>
              {detail.summary && (
                <p>
                  Median synthetic decode:{" "}
                  {detail.summary.metrics.decode_tokens_per_second.median.toFixed(
                    2,
                  )}{" "}
                  tokens/sec · {detail.summary.sample_count} measured fixture
                  trials (warmup excluded).
                </p>
              )}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Phase / trial</th>
                      <th>Synthetic TTFT (ms)</th>
                      <th>Synthetic prefill (tokens/sec)</th>
                      <th>Synthetic decode (tokens/sec)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.samples.map((s) => (
                      <tr key={s.phase + s.trial}>
                        <td>
                          {s.phase} / {s.trial + 1}
                        </td>
                        <td>{s.ttft_ms.toFixed(2)}</td>
                        <td>{s.prefill_tokens_per_second.toFixed(2)}</td>
                        <td>{s.decode_tokens_per_second.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details>
                <summary>Job event history</summary>
                <ul>
                  {detail.events.map((e, i) => (
                    <li key={i}>
                      {e.event} · {new Date(e.created_at).toLocaleString()}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </article>
      )}
    </section>
  );
}
