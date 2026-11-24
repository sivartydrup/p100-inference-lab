import { useEffect, useRef, useState } from 'react';

export type ControlState = { enabled: boolean; available: boolean; version?: string;
  safety_override?: string | null; channels: Record<string, { mode: number; pwm: number; rpm: number }> };
type Change = { channel: string; mode: 'full' | 'auto' | 'manual' } | { channel: string; pwm: number };

export function useFanControls(token: string, stale: boolean) {
  const [state, setState] = useState<ControlState | null>(null);
  const [credential, setCredential] = useState('');
  const [unlocked, setUnlocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);
  const received = useRef(0);
  const running = useRef(false);
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => {
    setUnlocked(false); setCredential(''); setState(null);
    return () => mutation.current?.abort();
  }, [token]);
  useEffect(() => {
    if (!token) return;
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    async function poll() {
      if (disposed) return;
      controller = new AbortController();
      const deadline = setTimeout(() => controller?.abort(), 5000);
      let delay = 2000;
      try {
        const response = await fetch('/api/fan-controls', { headers: { Authorization: 'Bearer ' + token },
          signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('Fan status unavailable');
        const next = await response.json() as ControlState;
        if (!disposed && !running.current) { setState(next); received.current = Date.now(); }
        if (!next.enabled) delay = 15000;
      } catch { if (!disposed && !running.current) { setState(null); received.current = 0; } }
      finally { clearTimeout(deadline); if (!disposed) timer = setTimeout(poll, delay); }
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, [token, refresh]);

  async function change(value: Change) {
    if (running.current || !unlocked || stale || !state?.available || !state.version || Date.now() - received.current > 5000) return;
    if ('pwm' in value && value.pwm === 0 && !window.confirm('Set ' + value.channel + ' output to 0? This may stop the cooling fan.')) return;
    running.current = true; setBusy(true); setMessage('Applying fan change…');
    const controller = new AbortController(); mutation.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/fan-controls', { method: 'POST', signal: controller.signal,
        headers: { Authorization: 'Bearer ' + token, 'X-Lab-Engine-Token': credential, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...value, version: state.version, confirmed: true }), cache: 'no-store' });
      if (!response.ok) {
        if (response.status === 403) { setCredential(''); setUnlocked(false); }
        throw new Error(response.status === 403 ? 'Fan control access denied. Check the engine control token.'
          : 'Fan change refused or outcome unknown. Check refreshed readings before trying again.');
      }
      const next = await response.json() as ControlState;
      if (controller.signal.aborted) return;
      setState(next); received.current = Date.now(); setMessage('Fan setting verified by the host.');
    } catch (error) {
      if (!controller.signal.aborted) setMessage(error instanceof Error && error.message.startsWith('Fan control access denied')
        ? error.message : 'Fan change refused or outcome unknown. Check refreshed readings before trying again.');
      else setMessage('Request timed out or was cancelled. Check refreshed readings before trying again.');
      setState(null); received.current = 0;
    } finally {
      clearTimeout(timeout); running.current = false; setBusy(false); setRefresh(value => value + 1);
    }
  }
  return { state, credential, setCredential, unlocked, unlock: () => setUnlocked(true), busy, message, change,
    lock: () => { setCredential(''); setUnlocked(false); },
    editable: unlocked && !busy && !stale && state?.available === true && Date.now() - received.current <= 5000 };
}

export function FanControl({ channel, mode, pwm, editable, busy, change }: {
  channel: string; mode?: number | null; pwm?: number | null; editable: boolean; busy: boolean;
  change: (value: Change) => Promise<void>;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const manual = mode === 1;
  const valid = pwm != null && Number.isInteger(pwm) && pwm >= 0 && pwm <= 255;
  const active = editable && !busy && valid && mode != null;
  async function commit(value: number) {
    if (!active || !manual) { setDraft(null); return; }
    try { if (value !== pwm) await change({ channel, pwm: value }); }
    finally { setDraft(null); }
  }
  return <div className="fan-controls">
    <div role="radiogroup" aria-label={channel + ' operating mode'} className="fan-mode-switch">
      {(['full', 'auto', 'manual'] as const).map((name, index) => <button type="button" role="radio" key={name}
        aria-checked={mode === [0, 2, 1][index]} disabled={!active}
        onClick={() => { if (mode !== [0, 2, 1][index]) { setDraft(null); void change({ channel, mode: name }); } }}>
        {name === 'full' ? 'Full' : name === 'auto' ? 'Auto' : 'Manual'}
      </button>)}
    </div>
    <label>{channel} output <output>{valid ? (draft ?? pwm) + ' / 255' : 'Unavailable'}</output>
      <input type="range" min="0" max="255" step="1" value={draft ?? (valid ? pwm : 0)!}
        disabled={!active || !manual} aria-label={channel + ' output'}
        onChange={event => setDraft(event.target.valueAsNumber)}
        onPointerUp={event => void commit(event.currentTarget.valueAsNumber)}
        onPointerCancel={() => setDraft(null)}
        onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) void commit(event.currentTarget.valueAsNumber); }}
        onBlur={event => { if (draft !== null) void commit(event.currentTarget.valueAsNumber); }} />
    </label>
    <p className="muted">{manual ? 'Release the slider to apply. Output level is not an RPM target.'
      : mode === 0 ? 'Full-speed override active; the slider shows the stored duty register.'
      : 'Live output; editing is available in Manual mode.'}</p>
  </div>;
}
