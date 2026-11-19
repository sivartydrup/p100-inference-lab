import type { NativeDetail, NativeJob } from "./NativeBenchmarks";

const number = (value: number | undefined | null, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";

function contextLabel(job: NativeJob) {
  const context = job.provenance.test_context;
  if (!context) return "Conditions not recorded";
  return `${context.gpu_uuids.length} declared GPU${context.gpu_uuids.length === 1 ? "" : "s"} · Case ${context.enclosure}`;
}

function MeasuredSessions({ jobs }: { jobs: NativeJob[] }) {
  const groups = new Map<string, NativeJob[]>();
  for (const job of jobs) {
    const session = job.provenance.test_context?.session_id;
    if (job.provenance.record_origin === "operator_import" && session)
      groups.set(session, [...(groups.get(session) || []), job]);
  }
  if (!groups.size) return null;
  return (
    <div aria-label="Measured GPU sessions">
      <h3>GPU test sessions</h3>
      <p className="muted">
        Session membership and case conditions are operator-declared. This view
        uses only the latest records returned by the history API. Missing
        records do not mean a GPU failed. Per-card medians are not combined
        throughput or PSU measurements.
      </p>
      {[...groups].map(([id, runs]) => {
        const context = runs[0].provenance.test_context!;
        const declared = context.gpu_uuids.map((g) => g.toLowerCase()).sort();
        const seen = runs
          .map((j) => j.provenance.gpu_uuid?.toLowerCase())
          .sort();
        const membership = JSON.stringify(seen) === JSON.stringify(declared);
        const consistent = runs.every((j) => {
          const c = j.provenance.test_context!;
          return (
            c.enclosure === context.enclosure &&
            JSON.stringify(c.gpu_uuids.map((g) => g.toLowerCase()).sort()) ===
              JSON.stringify(declared)
          );
        });
        const workload =
          !!runs[0].provenance.session_workload_key &&
          runs.every(
            (j) =>
              j.provenance.session_workload_key ===
              runs[0].provenance.session_workload_key,
          );
        const starts = runs.map((j) => Date.parse(j.started_at || ""));
        const ends = runs.map(
          (j, i) => starts[i] + (j.provenance.observed_seconds ?? NaN) * 1000,
        );
        const overlap = (Math.min(...ends) - Math.max(...starts)) / 1000;
        const comparable =
          membership &&
          consistent &&
          workload &&
          Number.isFinite(overlap) &&
          overlap > 0;
        return (
          <article key={id}>
              <h4>Case {context.enclosure} · {declared.length} GPUs</h4>
            <p>{contextLabel(runs[0])}</p>
            {comparable ? (
              <p>
                All declared GPUs present · Common measured window:{" "}
                {number(overlap, 1)} s.{" "}
                {runs.every(
                  (j) => j.status === "succeeded" && !j.summary?.partial,
                )
                  ? "Each card completed its target window."
                  : "Includes failed or partial measurements; this is not a passed session."}
              </p>
            ) : (
              <p className="error">
                Session comparison unavailable: records are missing, duplicated,
                inconsistent, use different workloads/drivers, or have no common
                measured window.
              </p>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>GPU UUID</th>
                    <th>Engine</th>
                    <th>Power cap</th>
                    <th>Window / outcome</th>
                    <th>Median decode tokens/sec</th>
                    <th>Peak sampled °C</th>
                    <th>Mean sampled W</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((j) => (
                    <tr key={j.id}>
                      <td>{j.provenance.gpu_uuid || "Unknown GPU"}</td>
                      <td>{j.provenance.engine}</td>
                      <td>
                        {number(j.provenance.declared_power_limit_w, 0)} W (
                        {j.provenance.power_limit_evidence === "sampled"
                          ? "logged"
                          : j.provenance.power_limit_evidence ===
                              "operator_declared"
                            ? "declared"
                            : "unknown"}
                        )
                      </td>
                      <td>
                        {number(j.provenance.observed_seconds, 1)} /{" "}
                        {number(j.provenance.target_seconds, 0)} s ·{" "}
                        {j.summary?.partial ? "Partial / " : ""}
                        {j.error_code || j.status}
                      </td>
                      <td>
                        {number(
                          j.summary?.metrics.decode_tokens_per_second?.median,
                        )}
                      </td>
                      <td>
                        {number(j.summary?.telemetry?.peak_temperature_c, 0)}
                      </td>
                      <td>{number(j.summary?.telemetry?.mean_power_w)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
              <details>
                <summary>Session identifier</summary>
                <p>{id}</p>
              </details>
          </article>
        );
      })}
    </div>
  );
}

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
      <MeasuredSessions jobs={jobs} />
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
                    <th>Conditions</th>
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
                      <td>{contextLabel(j)}</td>
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
      <p>
        GPU: {p.gpu_uuid || "Not recorded"}. {contextLabel(job)}.{" "}
        {p.test_context &&
          "Conditions are operator-declared, not independently attested."}
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
