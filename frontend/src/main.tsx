import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { BenchmarkLab, type Job, type Presets } from "./BenchmarkLab";
import { NativeBenchmarks } from "./NativeBenchmarks";
type GPU = {
  index: string;
  name: string;
  memory_total_mib: number | null;
  memory_used_mib: number | null;
  temperature_c: number | null;
  power_w: number | null;
  utilization_percent: number | null;
};
type System = {
  mode: "mock" | "live";
  scope: string;
  cpu_percent: number;
  memory: { percent: number; used: number; total: number };
  disk: { percent: number };
  gpus: { available: boolean; devices: GPU[]; message: string | null };
  sampled_at: string;
};
type Engine = { id: string; unit: string; state: string; note: string };
type Model = { root: number; name: string; bytes: number };
type Bench = {
  id: number;
  created_at: string;
  engine: string;
  model: string;
  tokens_per_second: number;
  source: string;
};
type Snapshot = {
  system: System;
  engines: { items: Engine[]; controls_enabled: boolean };
  models: { items: Model[]; warnings: string[]; truncated: boolean };
  benchmarks: { items: Bench[] };
  jobs: { items: Job[] };
  presets: Presets;
};
const POLL_INTERVAL_MS = 5_000;
const REQUEST_TIMEOUT_MS = 10_000;
const STALE_AFTER_MS = 15_000;

function App() {
  const [token, setToken] = useState(""),
    [connected, setConnected] = useState(false),
    [data, setData] = useState<Snapshot | null>(null),
    [error, setError] = useState(""),
    [receivedAt, setReceivedAt] = useState<number | null>(null),
    [now, setNow] = useState(Date.now());
  const [refreshSequence, setRefreshSequence] = useState(0);
  async function api(path: string, signal: AbortSignal) {
    const response = await fetch("/api/" + path, {
      signal,
      headers: {
        Authorization: "Bearer " + token,
        "Content-Type": "application/json",
      },
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "Authentication failed. Check your token."
          : "Request failed (" + response.status + "). Check the backend.",
      );
    return response.json();
  }
  useEffect(() => {
    if (!connected) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let activeRequest: AbortController | undefined;
    const updateAge = () => setNow(Date.now());
    const ageTimer = setInterval(updateAge, 1_000);
    document.addEventListener("visibilitychange", updateAge);
    const poll = async () => {
      const controller = new AbortController();
      activeRequest = controller;
      const startedAt = Date.now();
      let timedOut = false;
      // One deadline covers the complete snapshot, including response bodies.
      const deadline = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, REQUEST_TIMEOUT_MS);
      try {
        const [system, engines, models, benchmarks, jobs, presets] =
          await Promise.all([
            api("system", controller.signal),
            api("engines", controller.signal),
            api("models", controller.signal),
            api("benchmarks", controller.signal),
            api("benchmark-jobs", controller.signal),
            api("benchmark-presets", controller.signal),
          ]);
        // Suspended tabs can delay timeout callbacks: reject late responses too.
        if (Date.now() - startedAt >= REQUEST_TIMEOUT_MS) {
          timedOut = true;
          throw new Error("Snapshot deadline exceeded");
        }
        if (!disposed) {
          setData({ system, engines, models, benchmarks, jobs, presets });
          const received = Date.now();
          setReceivedAt(received);
          setNow(received);
          setError("");
        }
      } catch (e) {
        if (!disposed)
          setError(
            timedOut
              ? "Refresh timed out after 10 seconds. Retrying automatically."
              : (e as Error).message,
          );
      } finally {
        clearTimeout(deadline);
        // Cancel sibling requests if one endpoint failed; never overlap polls.
        controller.abort();
        if (!disposed) timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearInterval(ageTimer);
      document.removeEventListener("visibilitychange", updateAge);
      activeRequest?.abort();
    };
  }, [connected, token, refreshSequence]);
  // Use local receipt time: the server's wall clock may differ from the browser.
  const stale = Boolean(
    data &&
      (error || receivedAt === null || now - receivedAt >= STALE_AFTER_MS),
  );
  const number = (n: number | null | undefined, suffix = "") =>
    n == null
      ? "Unavailable"
      : n.toLocaleString(undefined, { maximumFractionDigits: 1 }) + suffix;
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          P100<span>INFERENCE LAB</span>
        </div>
        <div className="phase">PHASE 02 / FIXTURE LAB</div>
        <nav>
          <a href="#overview">Overview</a>
          <a href="#engines">Engine services</a>
          <a href="#models">Model library</a>
          <a href="#history">Benchmark history</a>
          <a href="#fixture-lab">Fixture lab</a>
          <a href="#native-benchmarks">Native history</a>
        </nav>
        <div className="aside-note">
          Native Ubuntu engines
          <br />
          Kmic · PXA · qr0n
          <br />
          <br />
          Target: 192.168.0.188
          <br />
          Browser dashboard / port 3000
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">YOUR HARDWARE. YOUR EXPERIMENTS.</p>
            <h1>Inference lab</h1>
            <p className="muted">
              One view of your host, engines and model library.
            </p>
          </div>
          <span className="badge">
            {connected && data
              ? stale
                ? "STALE / NO LIVE DATA"
                : data.system.mode === "mock"
                  ? "MOCK DEVELOPMENT DATA"
                  : "LIVE HOST DATA"
              : "DISCONNECTED / NO LIVE DATA"}
          </span>
        </header>
        {!connected ? (
          <section className="login">
            <h2>Connect to your lab</h2>
            <p>
              Enter the backend access token. It stays in memory for this tab
              only.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setConnected(true);
              }}
            >
              <label>
                Access token
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  minLength={32}
                  required
                  onChange={(e) => setToken(e.target.value)}
                />
              </label>
              <button>Connect</button>
            </form>
            <p className="muted">
              On a trusted LAN, open http://192.168.0.188:3000. HTTP is
              unencrypted; use an SSH tunnel or HTTPS on untrusted networks.
            </p>
          </section>
        ) : (
          <>
            <div className="toolbar">
              <span className="muted">
                Refresh every 5 seconds ·{" "}
                {data
                  ? new Date(data.system.sampled_at).toLocaleString()
                  : "Waiting for host"}
              </span>
              <button
                className="secondary"
                onClick={() => {
                  setConnected(false);
                  setToken("");
                  setData(null);
                  setError("");
                  setReceivedAt(null);
                }}
              >
                Disconnect & clear token
              </button>
            </div>
            {(error || stale) && (
              <p id="telemetry-warning" role="alert" className="error">
                {error || "No fresh sample received in 15 seconds."}{" "}
                {data && "Showing the last successful sample; data is stale."}
              </p>
            )}
            {data && (
              <>
                <section id="overview" className="stats">
                  <article>
                    <small>CPU UTILIZATION</small>
                    <strong>{number(data.system.cpu_percent, "%")}</strong>
                    <progress max="100" value={data.system.cpu_percent} />
                  </article>
                  <article>
                    <small>SYSTEM MEMORY</small>
                    <strong>{number(data.system.memory.percent, "%")}</strong>
                    <p>
                      {number(data.system.memory.used / 2 ** 30, " GiB")} of{" "}
                      {number(data.system.memory.total / 2 ** 30, " GiB")}
                    </p>
                  </article>
                  <article>
                    <small>LAB DISK USED</small>
                    <strong>{number(data.system.disk.percent, "%")}</strong>
                    <p>Filesystem holding the history database</p>
                  </article>
                  <article>
                    <small>DETECTED GPUS</small>
                    <strong>{data.system.gpus.devices.length}</strong>
                    <p>
                      {data.system.gpus.message || "Read-only NVIDIA telemetry"}
                    </p>
                  </article>
                </section>
                <section>
                  <div className="section-title">
                    <h2>GPU health</h2>
                    <span className="muted">No power or clock changes</span>
                  </div>
                  <div className="gpu-grid">
                    {data.system.gpus.devices.map((g) => (
                      <article key={g.index}>
                        <small>GPU {g.index}</small>
                        <h3>{g.name}</h3>
                        <dl>
                          <dt>VRAM</dt>
                          <dd>
                            {number(g.memory_used_mib, " MiB")} /{" "}
                            {number(g.memory_total_mib, " MiB")}
                          </dd>
                          <dt>Temperature</dt>
                          <dd>{number(g.temperature_c, " °C")}</dd>
                          <dt>Power</dt>
                          <dd>{number(g.power_w, " W")}</dd>
                          <dt>Utilization</dt>
                          <dd>{number(g.utilization_percent, "%")}</dd>
                        </dl>
                      </article>
                    ))}
                  </div>
                  {!data.system.gpus.devices.length && (
                    <p className="empty">
                      No GPUs reported. The lab remains usable before the P100s
                      are installed.
                    </p>
                  )}
                </section>
                <section id="engines">
                  <div className="section-title">
                    <h2>Engine services</h2>
                    <span className="badge">
                      {data.engines.controls_enabled
                        ? "CONTROLS ENABLED"
                        : "READ ONLY"}
                    </span>
                  </div>
                  <div className="engine-grid">
                    {data.engines.items.map((e) => (
                      <article key={e.id}>
                        <div className="section-title">
                          <h3>
                            {e.id === "kmic"
                              ? "Kmic"
                              : e.id === "pxa"
                                ? "PXA"
                                : "qr0n"}
                          </h3>
                          <span
                            className={
                              e.state === "active" ? "state active" : "state"
                            }
                          >
                            {e.state}
                          </span>
                        </div>
                        <code>{e.unit}</code>
                        <p className="muted">{e.note}</p>
                        <div className="actions">
                          {["start", "stop"].map((action) => (
                            <button
                              className="secondary"
                              key={action}
                              disabled
                              title="Engine controls are planned for a later phase"
                            >
                              {action}
                            </button>
                          ))}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
                <section id="models">
                  <h2>Model library</h2>
                  <p className="muted">
                    Read-only inventory of configured model roots. Files are
                    never loaded or modified.
                  </p>
                  {data.models.warnings.map((w, i) => (
                    <p key={i} className="error">
                      {w}
                    </p>
                  ))}
                  {data.models.truncated && (
                    <p>Inventory scan limit reached.</p>
                  )}
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Model file</th>
                          <th>Root</th>
                          <th>Size</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.models.items.map((m) => (
                          <tr key={m.root + ":" + m.name}>
                            <td>{m.name}</td>
                            <td>{m.root}</td>
                            <td>{number(m.bytes / 2 ** 30, " GiB")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!data.models.items.length && (
                    <p className="empty">
                      No model files found. Configure LAB_MODEL_ROOTS on the
                      backend.
                    </p>
                  )}
                </section>
                <section id="history">
                  <div className="section-title">
                    <h2>Benchmark history</h2>
                    <span className="muted">Manual measurements only</span>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Recorded</th>
                          <th>Engine / model</th>
                          <th>Tokens / sec</th>
                          <th>Source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.benchmarks.items.map((b) => (
                          <tr key={b.id}>
                            <td>{new Date(b.created_at).toLocaleString()}</td>
                            <td>
                              {b.engine} / {b.model}
                            </td>
                            <td>{number(b.tokens_per_second)}</td>
                            <td>{b.source}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!data.benchmarks.items.length && (
                    <p className="empty">
                      Your experiment history starts here. Add measured results
                      through the authenticated API.
                    </p>
                  )}
                </section>
                <NativeBenchmarks token={token} />
                <BenchmarkLab
                  token={token}
                  jobs={data.jobs.items}
                  presets={data.presets}
                  stale={stale}
                  onChange={() => setRefreshSequence((n) => n + 1)}
                />
              </>
            )}
          </>
        )}
        <footer>
          P100 Inference Lab / Phase 2 foundation · No inference requests are
          issued by this dashboard.
        </footer>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
