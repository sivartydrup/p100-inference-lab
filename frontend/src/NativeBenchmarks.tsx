import { useEffect, useRef, useState } from "react";
import { MeasuredComparison, MeasuredDetails } from "./MeasuredHistory";

export type NativeJob = {
  id: string;
  status: string;
  created_at: string;
  source: "native";
  error_code: string | null;
  preset_id: string;
  provenance: {
    engine: string;
    model_id: string;
    record_origin?: string;
    comparison_key?: string;
    observed_seconds?: number;
    target_seconds?: number;
    declared_power_limit_w?: number | null;
    power_limit_evidence?: string;
    gpu_name?: string;
    driver_version?: string;
    workload?: {
      prompt_tokens: number;
      output_tokens: number;
      context: number;
    };
  };
  summary: null | {
    sample_count: number;
    metrics: Record<string, { median: number }>;
    partial?: boolean;
    telemetry?: {
      peak_temperature_c: number;
      mean_power_w: number;
      peak_power_w: number;
      sampled_peak_vram_mib: number;
      measured_samples: number;
    };
  };
};
export type NativeDetail = NativeJob & {
  attempts: {
    phase: string;
    trial: number;
    sample: null | {
      prefill_tokens_per_second?: number;
      decode_tokens_per_second?: number;
      process_wall_ms?: number;
      first_text_ms?: number;
      request_wall_ms?: number;
      timestamp?: string;
      temperature_c?: number;
      power_w?: number;
      elapsed_measured_seconds?: number | null;
    };
  }[];
};

function useHistory<T>(path: string | null, token: string) {
  const [snapshot, setSnapshot] = useState<{
    path: string;
    value: T;
    received: number;
  } | null>(null);
  const [failure, setFailure] = useState<{
    path: string;
    message: string;
  } | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!path) return;
    let disposed = false,
      timer: ReturnType<typeof setTimeout> | undefined;
    let active: AbortController | undefined;
    const updateAge = () => setNow(Date.now());
    const age = setInterval(updateAge, 1_000);
    document.addEventListener("visibilitychange", updateAge);
    async function refresh() {
      const controller = new AbortController();
      active = controller;
      const started = Date.now(),
        deadline = setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await fetch("/api/" + path, {
          headers: { Authorization: "Bearer " + token },
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok)
          throw new Error(
            "Native history is unavailable (" + response.status + ").",
          );
        const value = await response.json();
        if (Date.now() - started >= 10_000)
          throw new Error("Native history request expired.");
        if (!disposed) {
          const received = Date.now();
          setSnapshot({ path: path!, value, received });
          setNow(received);
          setFailure(null);
        }
      } catch (e) {
        if (!disposed)
          setFailure({ path: path!, message: (e as Error).message });
      } finally {
        clearTimeout(deadline);
        if (!disposed) timer = setTimeout(refresh, 5_000);
      }
    }
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearInterval(age);
      document.removeEventListener("visibilitychange", updateAge);
      active?.abort();
    };
  }, [path, token]);
  const data = snapshot?.path === path ? snapshot.value : null;
  const error = failure?.path === path ? failure.message : "";
  const stale = !!data && (!!error || now - snapshot!.received >= 15_000);
  return { data, error, stale };
}

export function NativeBenchmarks({ token }: { token: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const list = useHistory<{
    configured: boolean;
    execution_available_via_api: false;
    items: NativeJob[];
  }>("native-benchmarks", token);
  const detail = useHistory<NativeDetail>(
    selected ? "native-benchmarks/" + selected : null,
    token,
  );
  const [exportError, setExportError] = useState("");
  const [busy, setBusy] = useState(false);
  const mounted = useRef(false),
    exporting = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      exporting.current?.abort();
    };
  }, []);
  async function download() {
    if (!selected || busy) return;
    const jobId = selected,
      controller = new AbortController(),
      started = Date.now();
    exporting.current = controller;
    setBusy(true);
    setExportError("");
    const deadline = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch(
        "/api/native-benchmarks/" + jobId + "/export",
        {
          headers: { Authorization: "Bearer " + token },
          signal: controller.signal,
          cache: "no-store",
        },
      );
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      if (!mounted.current || Date.now() - started >= 10_000) return;
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "native-benchmark-" + jobId + ".json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch {
      if (mounted.current)
        setExportError(
          "Export unavailable; retry when the connection is restored.",
        );
    } finally {
      clearTimeout(deadline);
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section id="native-benchmarks">
      <div className="section-title">
        <h2>Native benchmark history</h2>
        <span className="badge">READ ONLY / HOST-ADMIN RUNNER</span>
      </div>
      <p className="muted">
        Native runs require approved presets and local administrator action on
        Ubuntu. This dashboard cannot start or cancel an engine.
      </p>
      {(list.error || (list.data?.configured && list.stale)) && (
        <p role="alert" className="error">
          {list.error || "Native history may be stale."} Retrying automatically.
        </p>
      )}
      {list.data && !list.data.configured && (
        <p className="empty">
          Native history is not connected. Real execution remains disabled by
          default.
        </p>
      )}
      {list.data?.configured && (
        <>
          <MeasuredComparison jobs={list.data.items} />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Engine / model</th>
                  <th>Status</th>
                  <th>Results</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((j) => (
                  <tr key={j.id}>
                    <td>{new Date(j.created_at).toLocaleString()}</td>
                    <td>
                      {j.provenance.engine} / {j.provenance.model_id}
                      <p>
                        {j.provenance.record_origin === "operator_import"
                          ? "Imported measurement"
                          : "Native runner"}
                      </p>
                    </td>
                    <td>
                      {j.status}
                      {j.error_code && <p>{j.error_code}</p>}
                    </td>
                    <td>
                      <button
                        className="secondary"
                        onClick={() => setSelected(j.id)}
                      >
                        View native results
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!list.data.items.length && <p>No native benchmark jobs recorded.</p>}
        </>
      )}
      {selected && (
        <article className="fixture-detail">
          <div className="section-title">
            <h3>Native run details</h3>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void download()}
            >
              Export native JSON
            </button>
          </div>
          {(detail.error || detail.stale) && (
            <p role="alert" className="error">
              {detail.error || "Native details may be stale."} Retrying
              automatically.
            </p>
          )}
          {exportError && (
            <p role="alert" className="error">
              {exportError}
            </p>
          )}
          {!detail.data && !detail.error && <p>Loading native details...</p>}
          {detail.data && (
            <>
              {detail.data.provenance.record_origin === "operator_import" ? (
                <MeasuredDetails job={detail.data} />
              ) : (
                <>
                  <p>
                    {detail.data.status}. CLI token microbenchmark; TTFT, answer
                    quality and peak VRAM are not measured.
                  </p>
                  {detail.data.summary && (
                    <p>
                      Median prefill:{" "}
                      {detail.data.summary.metrics.prefill_tokens_per_second.median.toFixed(
                        2,
                      )}{" "}
                      tokens/sec. Median decode:{" "}
                      {detail.data.summary.metrics.decode_tokens_per_second.median.toFixed(
                        2,
                      )}{" "}
                      tokens/sec. {detail.data.summary.sample_count} measured
                      trials, warmup excluded.
                    </p>
                  )}
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Phase / trial</th>
                          <th>Prefill tokens/sec</th>
                          <th>Decode tokens/sec</th>
                          <th>Process wall ms</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.data.attempts.map((a) => (
                          <tr key={a.phase + a.trial}>
                            <td>
                              {a.phase} / {a.trial + 1}
                            </td>
                            <td>
                              {a.sample?.prefill_tokens_per_second?.toFixed(
                                2,
                              ) || "Not completed"}
                            </td>
                            <td>
                              {a.sample?.decode_tokens_per_second?.toFixed(2) ||
                                "Not completed"}
                            </td>
                            <td>
                              {a.sample?.process_wall_ms?.toFixed(2) ||
                                "Not completed"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
        </article>
      )}
    </section>
  );
}
