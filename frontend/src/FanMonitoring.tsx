import { useState } from "react";
import { FanControl, useFanControls } from './FanControls';

export type Fans = {
  available: boolean;
  source: "hwmon" | "mock";
  controls_enabled: false;
  message: string;
  partial: boolean;
  truncated: boolean;
  devices: { id: string; controller: string; device: string; channel: string; rpm: number | null; pwm?: number | null; pwm_enable?: number | null }[];
};

export function FanMonitoring({ fans, stale, token = '' }: { fans?: Fans; stale: boolean; token?: string }) {
  const [showZero, setShowZero] = useState(false);
  const controls = useFanControls(token, stale);
  const target = (fan: Fans['devices'][number]) => fans?.source === 'hwmon' && fan.controller === 'it8620' &&
    fan.device === 'it87.2624' && ['fan1', 'fan2'].includes(fan.channel);
  const keepVisible = (fan: Fans['devices'][number]) => target(fan) && controls.state?.enabled;
  const zeroCount = fans?.devices.filter(fan => fan.rpm === 0 && !keepVisible(fan)).length || 0;
  const visibleFans = fans?.devices.filter(fan => showZero || fan.rpm !== 0 || keepVisible(fan)) || [];
  return <section id="fans" aria-label="Fan monitoring">
    <div className="section-title">
      <h2>Fan monitoring</h2>
      <span className="badge">{stale ? "STALE READINGS" : controls.state?.enabled ? 'AUTHENTICATED CONTROLS' : "READ ONLY"}</span>
    </div>
    <p className="muted">
      {fans?.source === "mock" ? "Simulated fan readings · Windows development data" : "Motherboard fan speeds"}
      {stale && " · Last received readings; current speeds are unknown."}
    </p>
    <p className={fans?.available ? "muted" : "empty"}>
      {fans?.message || "Fan monitoring is not reported by this API version."}
    </p>
    {controls.state?.enabled && <div className="engine-access">
      {!controls.unlocked ? <form onSubmit={event => { event.preventDefault(); if (controls.credential.length >= 32) controls.unlock(); }}>
        <label>Fan control token <input type="password" autoComplete="off" value={controls.credential}
          onChange={event => controls.setCredential(event.target.value)} /></label>
        <button disabled={controls.credential.length < 32 || stale}>Unlock fan controls</button>
        <p className="muted">Use the separate engine control token. It stays in this tab only.</p>
      </form> : <button className="secondary" disabled={controls.busy} onClick={controls.lock}>Lock &amp; clear fan token</button>}
      <p className="muted">Only IT8620 fan1/fan2 can be changed. The host forces full speed at 75°C or on monitoring failure.</p>
      {controls.state.safety_override && <p role="alert">{controls.state.safety_override}</p>}
    </div>}
    {controls.message && <p role="status">{controls.message}</p>}
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
        {target(fan) && <FanControl channel={fan.channel}
          mode={controls.state?.channels[fan.channel]?.mode ?? fan.pwm_enable}
          pwm={controls.state?.channels[fan.channel]?.pwm ?? fan.pwm}
          editable={controls.editable} busy={controls.busy} change={controls.change} />}
      </article>)}
    </div>}
    <p className="muted">{controls.state?.enabled ? 'Other channels remain read-only. Header assignments are unverified.'
      : 'Monitoring only. Fan speed controls are not available here.'}</p>
  </section>;
}
