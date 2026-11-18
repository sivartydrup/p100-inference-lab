import type { NativeDetail, NativeJob } from "./NativeBenchmarks";

const number = (value: number | undefined | null, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";

export function MeasuredComparison({ jobs }: { jobs: NativeJob[] }) {
  const groups = new Map<string, NativeJob[]>();
  for (const job of jobs) {
    if (
      job.provenance.record_origin !== "operator_import" ||
      !job.provenance.comparison_key
    )
      continue;
    const key = job.provenance.comparison_key;
    groups.set(key, [...(groups.get(key) || []), job]);
  }
  if (!groups.size) return null;
  return (
    <div aria-label="Measured run comparison">
      <h3>Measured runs by workload</h3>
      <p className="muted">
        Operator-imported history. Groups share model hashes, prompt/settings,
        GPU and driver. Compare the power cap, outcome and duration too; these
        are individual runs, not a controlled ranking.
      </p>
      {[...groups.entries()].map(([key, runs]) => {
        const p = runs[0].provenance;
        return (
          <article key={key}>
            <h4>{p.model_id}</h4>
            <p>
              {p.gpu_name} · Driver {p.driver_version} · Prompt{" "}
              {p.workload?.prompt_tokens} / output {p.workload?.output_tokens}{" "}
              tokens · Context {p.workload?.context}
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Engine</th>
                    <th>Power cap</th>
                    <th>Window / outcome</th>
                    <th>Median decode tokens/sec</th>
                    <th>First text ms</th>
                    <th>Peak sampled °C</th>
                    <th>Mean / peak sampled W</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((j) => (
                    <tr key={j.id}>
                      <td>{j.provenance.engine}</td>
                      <td>
                        {number(j.provenance.declared_power_limit_w, 0)} W{" "}
                        <span>
                          (
                          {j.provenance.power_limit_evidence === "sampled"
                            ? "logged"
                            : j.provenance.power_limit_evidence ===
                                "operator_declared"
                              ? "declared"
                              : "unknown"}
                          )
                        </span>
                      </td>
                      <td>
                        {number(j.provenance.observed_seconds, 1)} /{" "}
                        {number(j.provenance.target_seconds, 0)} s<br />
                        {j.summary?.partial
                          ? "Partial / " + (j.error_code || j.status)
                          : j.status}
                      </td>
                      <td>
                        {number(
                          j.summary?.metrics.decode_tokens_per_second?.median,
                        )}
                      </td>
                      <td>
                        {number(j.summary?.metrics.first_text_ms?.median)}
                      </td>
                      <td>
                        {number(j.summary?.telemetry?.peak_temperature_c, 0)}
                      </td>
                      <td>
                        {number(j.summary?.telemetry?.mean_power_w)} /{" "}
                        {number(j.summary?.telemetry?.peak_power_w)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function MeasuredDetails({ job }: { job: NativeDetail }) {
  const summary = job.summary,
    p = job.provenance;
  const readings = job.attempts.filter((a) => a.phase === "telemetry");
  const requests = job.attempts.filter(
    (a) => a.phase === "warmup" || a.phase === "measured",
  );
  return (
    <div aria-label="Imported measurement details">
      <p>
        Resident streaming measurement, imported by a local operator. First-text
        latency is request start to first nonempty text over a local Unix
        socket; it is not exact raw-token TTFT or Windows-browser latency.
      </p>
      <p>
        Observed {number(p.observed_seconds, 1)} of{" "}
        {number(p.target_seconds, 0)} seconds. {summary?.sample_count} measured
        requests; warmup excluded.
      </p>
      {summary?.partial && (
        <p role="alert" className="error">
          Partial run: {job.error_code || job.status}. This did not complete the
          target window.
        </p>
      )}
      <p>
        Median prefill:{" "}
        {number(summary?.metrics.prefill_tokens_per_second?.median)} tokens/sec.
        Median decode:{" "}
        {number(summary?.metrics.decode_tokens_per_second?.median)} tokens/sec.
        Median first text: {number(summary?.metrics.first_text_ms?.median)} ms.
      </p>
      <p>
        Peak sampled temperature:{" "}
        {number(summary?.telemetry?.peak_temperature_c, 0)}°C. Mean / peak
        sampled board power: {number(summary?.telemetry?.mean_power_w)} /{" "}
        {number(summary?.telemetry?.peak_power_w)} W. Sampled peak VRAM:{" "}
        {number(summary?.telemetry?.sampled_peak_vram_mib, 0)} MiB.
      </p>
      <p className="muted">
        Samples can miss brief peaks. A power-cap setting is separate from
        observed draw. These records do not measure answer quality or energy at
        the wall socket. Imported history does not attest native-runner
        execution or grant permission to run an engine.
      </p>
      <h4>Request samples</h4>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Phase / request</th>
              <th>Prefill tokens/sec</th>
              <th>Decode tokens/sec</th>
              <th>First text ms</th>
              <th>Request wall ms</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((a) => (
              <tr key={a.phase + a.trial}>
                <td>
                  {a.phase} / {a.trial + 1}
                </td>
                <td>{number(a.sample?.prefill_tokens_per_second)}</td>
                <td>{number(a.sample?.decode_tokens_per_second)}</td>
                <td>{number(a.sample?.first_text_ms)}</td>
                <td>{number(a.sample?.request_wall_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>Recent telemetry</h4>
      <p>
        Showing the last {Math.min(20, readings.length)} of {readings.length}{" "}
        readings. JSON export includes every sample and evidence hash.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Measured seconds</th>
              <th>Temperature °C</th>
              <th>Board power W</th>
            </tr>
          </thead>
          <tbody>
            {readings.slice(-20).map((a) => (
              <tr key={a.trial}>
                <td>{a.sample?.timestamp}</td>
                <td>{number(a.sample?.elapsed_measured_seconds, 1)}</td>
                <td>{number(a.sample?.temperature_c, 0)}</td>
                <td>{number(a.sample?.power_w)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Build and evidence</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {JSON.stringify(p, null, 2)}
        </pre>
      </details>
    </div>
  );
}
