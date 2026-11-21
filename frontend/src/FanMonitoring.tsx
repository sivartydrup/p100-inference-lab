import { useState } from "react";

export type Fans = {
  available: boolean;
  source: "hwmon" | "mock";
  controls_enabled: false;
  message: string;
  partial: boolean;
  truncated: boolean;
  devices: { id: string; controller: string; device: string; channel: string; rpm: number | null }[];
};

export function FanMonitoring({ fans, stale }: { fans?: Fans; stale: boolean }) {
  const [showZero, setShowZero] = useState(false);
  const zeroCount = fans?.devices.filter(fan => fan.rpm === 0).length || 0;
  const visibleFans = fans?.devices.filter(fan => showZero || fan.rpm !== 0) || [];
  return <section id="fans" aria-label="Fan monitoring">
    <div className="section-title">
      <h2>Fan monitoring</h2>
      <span className="badge">{stale ? "STALE READINGS" : "READ ONLY"}</span>
    </div>
    <p className="muted">
      {fans?.source === "mock" ? "Simulated fan readings · Windows development data" : "Motherboard fan speeds"}
      {stale && " · Last received readings; current speeds are unknown."}
    </p>
    <p className={fans?.available ? "muted" : "empty"}>
      {fans?.message || "Fan monitoring is not reported by this API version."}
    </p>
    {zeroCount > 0 && <div className="section-title">
      <span className="muted">{zeroCount} zero-RPM {zeroCount === 1 ? "input" : "inputs"} {showZero ? "shown" : "hidden"}</span>
      <button className="secondary" aria-pressed={showZero} onClick={() => setShowZero(value => !value)}>
        {showZero ? "Hide zero-RPM inputs" : "Show zero-RPM inputs"}
      </button>
    </div>}
    {zeroCount > 0 && !showZero && visibleFans.length === 0 &&
      <p className="empty">No rotating fans reported. Use “Show zero-RPM inputs” to inspect the readings.</p>}
    {visibleFans.length > 0 && <div className="gpu-grid">
      {visibleFans.map(fan => <article key={fan.id}>
        <small>{fan.controller} · {fan.device}</small>
        <h3>{fan.channel}</h3>
        <p className="fan-rpm">{fan.rpm === null ? "Unavailable" : fan.rpm.toLocaleString() + " RPM"}</p>
        {fan.rpm === 0 && <p className="muted">No rotation reported; this may be an unused input or a missing speed signal.</p>}
      </article>)}
    </div>}
    <p className="muted">Monitoring only. Fan speed controls are not available here.</p>
  </section>;
}
