import { useState } from 'react';

export type Engine = {
  id: string; unit: string; state: string; note: string;
  ready?: boolean; model?: string | null; configured_model?: string;
  boot_enabled?: boolean; boot_state?: string; controls_available?: boolean;
};
type Action = 'start' | 'stop' | 'enable_boot' | 'disable_boot';

export function EngineServices({ items, controlsEnabled, token, stale, refresh }: {
  items: Engine[]; controlsEnabled: boolean; token: string; stale: boolean; refresh: () => void;
}) {
  const [key, setKey] = useState('');
  const [unlocked, setUnlocked] = useState(false);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const kmic = items.find(item => item.id === 'kmic');
  const permitted = controlsEnabled && kmic?.controls_available === true && unlocked && !stale && !busy;
  async function execute(action: Action) {
    if (!permitted) return;
    setBusy(true); setPending(null); setMessage('Sending engine request…');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12_000);
    try {
      const response = await fetch('/api/engines/kmic/control', { method: 'POST', signal: controller.signal,
        headers: { Authorization: 'Bearer ' + token, 'X-Lab-Engine-Token': key, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }), cache: 'no-store' });
      if (!response.ok) {
        if (response.status === 403) { setUnlocked(false); setKey(''); }
        throw new Error(response.status === 403 ? 'Control access denied. Check your engine control token.' :
          'Request failed or outcome is unknown. Check refreshed status before trying again.');
      }
      setMessage(action === 'enable_boot' || action === 'disable_boot'
        ? 'Boot setting request accepted. The running engine is unchanged; checking status.'
        : 'Service request accepted. Checking status; model loading may take time.');
    } catch (error) {
      setMessage(error instanceof Error && error.name !== 'AbortError' ? error.message :
        'Request timed out; outcome is unknown. Check refreshed status before trying again.');
    } finally { clearTimeout(timer); controller.abort(); setBusy(false); refresh(); }
  }
  const descriptions: Record<Action, string> = {
    start: 'Start the existing Kmic service and its configured model?',
    stop: 'Stop Kmic? This will interrupt active inference requests and unload its model.',
    enable_boot: 'Automatically start Kmic at future boots? This does not start it now.',
    disable_boot: 'Disable automatic Kmic startup at future boots? This does not stop it now.',
  };
  return <section id="engines" aria-label="Engine services">
    <div className="section-title"><h2>Engine services</h2>
      <span className="badge">{controlsEnabled ? 'AUTHENTICATED CONTROLS' : 'READ ONLY'}</span></div>
    {controlsEnabled && <div className="engine-access">
      {!unlocked ? <form onSubmit={event => { event.preventDefault(); if (key.length >= 32) setUnlocked(true); }}>
        <label>Engine control token <input type="password" value={key} autoComplete="off"
          onChange={event => setKey(event.target.value)} /></label>
        <button disabled={key.length < 32 || busy}>Unlock controls</button>
        <p className="muted">This separate token stays in memory and is checked when you submit an action.</p>
      </form> : <button className="secondary" disabled={busy} onClick={() => { setUnlocked(false); setKey(''); setPending(null); }}>Lock &amp; clear control token</button>}
    </div>}
    {message && <p role="status">{message}</p>}
    {pending && <div className="empty" role="group" aria-label="Confirm engine action">
      <p>{descriptions[pending]}</p>
      <button disabled={!permitted} onClick={() => void execute(pending)}>Confirm {pending.replace('_', ' ')}</button>{' '}
      <button className="secondary" onClick={() => setPending(null)}>Cancel</button>
    </div>}
    <div className="engine-grid">{items.map(engine => {
      const managed = engine.id === 'kmic' && engine.controls_available !== undefined;
      return <article key={engine.id}>
        <div className="section-title"><h3>{engine.id === 'kmic' ? 'Kmic' : engine.id === 'pxa' ? 'PXA' : 'qr0n'}</h3>
          <span className={engine.state === 'active' && !stale ? 'state active' : 'state'}>{stale ? engine.state + ' (stale)' : engine.state}</span></div>
        <code>{engine.unit}</code>
        <p className="muted">{engine.note}</p>
        {managed && <dl>
          <dt>Inference readiness</dt><dd>{stale ? 'Unknown — stale data' : engine.ready ? 'Ready' : 'Not ready'}</dd>
          <dt>Reported model</dt><dd>{engine.model || 'Not reported'}</dd>
          {engine.configured_model && <><dt>Configured model</dt><dd>{engine.configured_model}</dd></>}
          <dt>Automatically start at boot</dt><dd>{engine.boot_enabled === undefined ? 'Unknown' : engine.boot_enabled ? 'Enabled' : 'Disabled'}{stale && ' (stale)'}</dd>
        </dl>}
        <div className="actions">{(['start', 'stop'] as Action[]).map(action => <button className="secondary" key={action}
          disabled={engine.id !== 'kmic' || !permitted || (action === 'start' ? engine.state !== 'inactive' && engine.state !== 'failed' : engine.state !== 'active' && engine.state !== 'activating')}
          onClick={() => setPending(action)}>{action}</button>)}</div>
        {managed && engine.boot_enabled !== undefined && <button className="secondary" disabled={!permitted}
          onClick={() => setPending(engine.boot_enabled ? 'disable_boot' : 'enable_boot')}>
          {engine.boot_enabled ? 'Disable automatic startup' : 'Enable automatic startup'}</button>}
      </article>;
    })}</div>
  </section>;
}
