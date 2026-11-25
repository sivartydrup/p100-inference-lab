import { useState, type FormEvent } from 'react';
import { downloadClientProfile, downloadGatewayProfile, type Recipe } from './clientProfile';
import { engineNames } from './engines';
import type { Engine } from './EngineServices';

export type Settings = { context: number; batch: number; ubatch: number };
export type Selection = { model_id: string; settings: Settings };
type Environment = { engine_version: string; gpu: string; gpu_count: number; quantization: string; bytes: number; sha256: string; recipe_id?: string; recipe_sha256?: string };
export type ApprovedModel = Environment & {
  id: string; name: string; available: boolean; engines: string[]; limits: Settings; initial: Settings; recipe?: Recipe;
};
type Evidence = { report_sha256: string; metric: string; value: number };
type Preset = Selection & {
  id: number; name: string; workload: string; is_default: boolean; environment: Environment; evidence: Evidence | null;
};
export type Library = {
  items: { root: number; name: string; bytes: number }[];
  warnings: string[]; truncated: boolean; presets?: Preset[];
};

const parameterNames = { context: 'Context tokens', batch: 'Batch tokens', ubatch: 'Microbatch tokens' };
const minimum = { context: 512, batch: 32, ubatch: 32 };
const fields = Object.keys(parameterNames) as (keyof Settings)[];
const gib = (bytes: number) => (bytes / 2 ** 30).toFixed(2) + ' GiB';
const quantization = (name: string) => name.match(/(?:^|[.-])((?:UD-)?(?:IQ|Q)[1-8](?:_[A-Z0-9]+)+|F16|BF16|F32)(?=[.-])/i)?.[1] ?? 'Unknown';

function sameEnvironment(model: ApprovedModel, preset: Preset) {
  return model.engine_version === preset.environment.engine_version && model.gpu === preset.environment.gpu &&
    model.gpu_count === preset.environment.gpu_count && model.quantization === preset.environment.quantization &&
    model.bytes === preset.environment.bytes && model.sha256 === preset.environment.sha256 &&
    model.recipe?.id === preset.environment.recipe_id && model.recipe?.sha256 === preset.environment.recipe_sha256;
}

export function ModelLibrary({ data, engines, catalog = [], canStage, stage, stageMessage, token, stale, refresh }: {
  data: Library; engines: Engine[]; catalog?: ApprovedModel[]; canStage: boolean; stage: (selection: Selection) => void;
  stageMessage: string; token: string; stale: boolean; refresh: () => void;
}) {
  const [selectedEngine, setEngine] = useState('kmic');
  const [modelId, setModelId] = useState('');
  const engine = engines.some(item => item.id === selectedEngine) ? selectedEngine : engines[0]?.id ?? '';
  const fixedEngines = engines.filter((item): item is Engine & { serving_profile: NonNullable<Engine['serving_profile']> } =>
    item.serving_profile !== undefined);
  const fixedEngine = fixedEngines.find(item => item.id === engine);
  const profile = fixedEngine?.serving_profile;
  const compatible = catalog.filter(model => model.engines.includes(engine));
  const chosen = compatible.find(model => model.id === modelId);
  const presets = data.presets ?? [];
  return <section id="models" aria-label="Model library">
    <h2>Model library</h2>
    {stageMessage && <p role="status">{stageMessage}</p>}
    <div className="table-wrap"><table>
      <thead><tr><th>Model</th><th>Quantization</th><th>Size</th><th>Availability</th><th>Compatible engines</th><th>Source</th></tr></thead>
      <tbody>
        {catalog.map(model => <tr key={model.id}>
          <td>{model.name}<br /><code>{model.id}</code></td><td>{model.quantization}</td>
          <td title={model.bytes.toLocaleString() + ' bytes'}>{gib(model.bytes)}</td>
          <td>{stale ? 'Unknown' : model.available ? 'Available' : 'Unavailable'}</td>
          <td>{model.engines.map(id => engineNames[id] ?? id).join(', ')}</td>
          <td>Host catalog</td>
        </tr>)}
        {fixedEngines.map(item => <tr key={item.id}>
          <td>{item.configured_model}<br /><code>{item.serving_profile.alias}</code></td>
          <td>{item.serving_profile.quantization}</td><td>—</td>
          <td>{stale ? 'Unknown' : item.ready ? 'Loaded' : item.state === 'active' ? 'Loading' : 'Configured'}</td>
          <td>{engineNames[item.id] ?? item.id}</td><td>Fixed service profile</td>
        </tr>)}
        {data.items.map(item => <tr key={item.root + ':' + item.name}>
          <td>{item.name}</td><td>{quantization(item.name)}</td>
          <td title={item.bytes.toLocaleString() + ' bytes'}>{gib(item.bytes)}</td><td>{stale ? 'Unknown' : 'Found'}</td><td>Unverified</td>
          <td>Scanned root {item.root}</td>
        </tr>)}
      </tbody>
    </table></div>
    {data.warnings.map((warning, index) => <p key={index} className="error">{warning}</p>)}
    {data.truncated && <p>Inventory scan limit reached.</p>}
    {!catalog.length && !fixedEngines.length && !data.items.length && <p className="empty">No model files found.</p>}
    <div className="model-fields">
      <label>Engine <select value={engine} onChange={event => { setEngine(event.target.value); setModelId(''); }}>
        {engines.map(item => <option key={item.id} value={item.id}>{engineNames[item.id] ?? item.id}</option>)}
      </select></label>
      <label>Approved model <select value={profile?.alias ?? chosen?.id ?? ''} onChange={event => setModelId(event.target.value)} disabled={!compatible.length}>
        {profile ? <option value={profile.alias}>{fixedEngine?.configured_model}</option> : <>
          <option value="">{compatible.length ? 'Select a model' : 'No approved models reported'}</option>
          {compatible.map(model => <option key={model.id} value={model.id}>{model.name}{model.available ? '' : ' (unavailable)'}</option>)}
        </>}
      </select></label>
    </div>
    {profile && <div className="model-profile">
      <dl>
        <dt>Gateway model ID</dt><dd><code>{profile.alias}</code></dd>
        <dt>Context</dt><dd>{profile.context.toLocaleString()} tokens</dd>
        <dt>Maximum output</dt><dd>{profile.max_output.toLocaleString()} tokens</dd>
        <dt>Input</dt><dd>{profile.vision ? 'Text and images' : 'Text'}</dd>
        <dt>Reasoning</dt><dd>{profile.reasoning ? 'Supported' : 'Off'}</dd>
      </dl>
      <p className="muted">This model is configured in the host service. Use Engine services to load it.</p>
      <button className="secondary" disabled={stale} onClick={() => downloadGatewayProfile(profile, fixedEngine?.configured_model ?? profile.alias)}>Download Oh My Pi preset</button>
    </div>}
    {chosen && <ProfileEditor key={chosen.id} model={chosen} presets={presets.filter(item => item.model_id === chosen.id)}
      canStage={canStage} stage={stage} token={token} stale={stale} refresh={refresh} />}
    {presets.some(preset => !catalog.some(model => model.id === preset.model_id)) && <details>
      <summary>Presets without a reported catalog entry</summary>
      <ul>{presets.filter(preset => !catalog.some(model => model.id === preset.model_id)).map(preset =>
        <li key={preset.id}>{preset.name} · {preset.model_id} {preset.is_default && '(default)'}</li>)}</ul>
    </details>}
  </section>;
}

function ProfileEditor({ model, presets, canStage, stage, token, stale, refresh }: {
  model: ApprovedModel; presets: Preset[]; canStage: boolean; stage: (selection: Selection) => void;
  token: string; stale: boolean; refresh: () => void;
}) {
  const defaultPreset = presets.find(preset => preset.is_default && sameEnvironment(model, preset));
  const [settings, setSettings] = useState(defaultPreset?.settings ?? model.initial);
  const [presetId, setPresetId] = useState(defaultPreset?.id ?? 0);
  const [name, setName] = useState('');
  const [workload, setWorkload] = useState(defaultPreset?.workload ?? '');
  const [makeDefault, setMakeDefault] = useState(false);
  const [evidence, setEvidence] = useState(false);
  const [digest, setDigest] = useState('');
  const [metric, setMetric] = useState('decode_tokens_per_second');
  const [result, setResult] = useState('');
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const chosen = presets.find(preset => preset.id === presetId);
  const settingsValid = fields.every(field => Number.isInteger(settings[field]) &&
    settings[field] >= minimum[field] && settings[field] <= model.limits[field]) &&
    settings.ubatch <= settings.batch && settings.batch <= settings.context &&
    (!model.recipe || fields.every(field => settings[field] === model.initial[field]));
  const ready = canStage && model.available && !stale && !busy && settingsValid;

  function usePreset(id: number) {
    const preset = presets.find(item => item.id === id);
    setPresetId(id);
    setSettings(preset?.settings ?? model.initial);
    setWorkload(preset?.workload ?? '');
    setEvidence(false);
    setDigest('');
    setResult('');
    setPending(false);
  }

  async function request(path: string, method: string, body?: unknown) {
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch(path, { method, signal: AbortSignal.timeout(12_000),
        headers: { Authorization: 'Bearer ' + token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'Preset request failed. Refresh and check the fields.');
      }
      if (method === 'POST') {
        const saved = await response.json() as Preset;
        setPresetId(saved.id);
        setName('');
        setMessage('Preset saved.');
      } else if (method === 'DELETE') {
        setPresetId(0);
        setMessage('Preset deleted.');
      } else {
        setMessage('Default preset updated.');
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Preset request failed.');
    } finally {
      setBusy(false);
      refresh();
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || stale || !settingsValid) return;
    void request('/api/model-presets', 'POST', {
      model_id: model.id, settings, name: name.trim(), workload: workload.trim(), make_default: makeDefault,
      evidence: evidence ? { report_sha256: digest, metric, value: Number(result) } : null,
    });
  }

  return <div>
    <p className="muted">Kmic {model.engine_version} · {model.gpu_count} × {model.gpu} · {model.quantization}</p>
    {model.recipe && <div className="empty" aria-label="Reviewed launch profile">
      <h3>{model.initial.context > 32768 ? 'Long-context' : model.recipe.mode} coding profile</h3>
      <p>{model.recipe.expert_cache_mib / 1024} GiB expert cache · GPU split {model.recipe.tensor_split.join(':')} · MTP drafts {model.recipe.draft_tokens} tokens</p>
      <p>{model.recipe.notice}</p>
      <p>This profile supplies the engine’s starting defaults. Coding clients can override sampling and thinking settings per request.</p>
      <button className="secondary" onClick={() => downloadClientProfile(model.recipe!, model.initial.context)}>Download Oh My Pi preset</button>
      <p>Merge the downloaded fragment into your Oh My Pi models.yml. Set its inference API key and check the server address.
        Use the selected profile’s served model alias. The dashboard access token is a separate credential.</p>
      <details><summary>Client request settings</summary><pre className="client-settings">{JSON.stringify(model.recipe.client_settings, null, 2)}</pre></details>
    </div>}
    <label className="model-fields">Load preset <select value={chosen?.id ?? 0} onChange={event => usePreset(Number(event.target.value))} disabled={busy || pending}>
      <option value={0}>Catalog starting values</option>
      {presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}{preset.is_default ? ' (default)' : ''}</option>)}
    </select></label>
    {chosen && <div className="empty">
      <p>{chosen.evidence ? 'Operator-reported benchmark evidence' : 'User-saved settings'}</p>
      <p>{chosen.environment.engine_version} · {chosen.environment.gpu_count} × {chosen.environment.gpu} · {chosen.environment.quantization}
        {' · '}{chosen.settings.context.toLocaleString()} context tokens</p>
      {chosen.workload && <p>Workload — {chosen.workload}</p>}
      {chosen.evidence && <><p>{chosen.evidence.value.toLocaleString()} {chosen.evidence.metric === 'decode_tokens_per_second' ? 'decode' : 'prefill'} tokens/s</p>
        <p>Report SHA-256 <code className="model-digest">{chosen.evidence.report_sha256}</code></p></>}
      {!sameEnvironment(model, chosen) && <p className="error">This preset was saved for a different catalog environment. Review the settings before using them.</p>}
      <div className="actions">
        <button className="secondary" disabled={busy || stale || pending || chosen.is_default}
          onClick={() => void request('/api/model-presets/' + chosen.id + '/default', 'PUT')}>Set as default</button>
        <button className="secondary" disabled={busy || stale || pending} onClick={() => {
          if (window.confirm('Delete the saved preset “' + chosen.name + '”?')) void request('/api/model-presets/' + chosen.id, 'DELETE');
        }}>Delete preset</button>
      </div>
    </div>}
    <form onSubmit={save} className="model-profile">
      <fieldset disabled={busy || stale || pending}>
        <legend>Inference parameters</legend>
        <div className="model-fields">{fields.map(field => <label key={field}>{parameterNames[field]}
          <input type="number" min={minimum[field]} max={model.limits[field]} value={Number.isNaN(settings[field]) ? '' : settings[field]}
            onChange={event => setSettings({ ...settings, [field]: event.target.valueAsNumber })} readOnly={!!model.recipe} required />
        </label>)}</div>
        <p className="muted">Microbatch ≤ batch ≤ context. Use the model’s reviewed limits.</p>
        {model.recipe && <p className="muted">These parameters are fixed to the tested profile. Further tuning requires a separately reviewed profile.</p>}
        <div className="model-fields">
          <label>Preset name <input value={name} maxLength={80} onChange={event => setName(event.target.value)} required /></label>
          <label>Workload <input value={workload} maxLength={240} onChange={event => setWorkload(event.target.value)} required={evidence} /></label>
        </div>
        <label><input type="checkbox" checked={makeDefault} onChange={event => setMakeDefault(event.target.checked)} /> Make default for this model</label>
        <label><input type="checkbox" checked={evidence} onChange={event => setEvidence(event.target.checked)} /> Attach benchmark evidence</label>
        {evidence && <div className="model-fields">
          <label>Report SHA-256 <input value={digest} pattern="[0-9a-f]{64}" maxLength={64} required onChange={event => setDigest(event.target.value)} /></label>
          <label>Metric <select value={metric} onChange={event => setMetric(event.target.value)}>
            <option value="decode_tokens_per_second">Decode tokens/s</option><option value="prefill_tokens_per_second">Prefill tokens/s</option>
          </select></label>
          <label>Measured tokens/s <input type="number" min="0" max="1000000" step="any" value={result} required onChange={event => setResult(event.target.value)} /></label>
        </div>}
        <div className="actions">
          <button disabled={!settingsValid || !name.trim()}>Save preset</button>
          <button type="button" className="secondary" disabled={!ready} onClick={() => setPending(true)}>Configure next start</button>
        </div>
      </fieldset>
    </form>
    {!canStage && <p className="muted">To configure the next start, stop Kmic using Engine services and unlock engine controls.</p>}
    {pending && <div className="empty" role="group" aria-label="Confirm next-start configuration">
      <p>Configure {model.name} for the next Kmic launch with {settings.context} context, {settings.batch} batch and {settings.ubatch} microbatch tokens?</p>
      <button disabled={!ready} onClick={() => { setPending(false); stage({ model_id: model.id, settings }); }}>Confirm next-start configuration</button>{' '}
      <button className="secondary" onClick={() => setPending(false)}>Cancel</button>
    </div>}
    {message && <p role="status">{message}</p>}
  </div>;
}
