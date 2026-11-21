import { expect, test } from '@playwright/test';
import { fixture, token } from './fixtures';
import { readFile } from 'node:fs/promises';

test('save and reuse an approved model preset, then explicitly stage it with the engine credential', async ({ page }) => {
  const settings = { context: 8192, batch: 512, ubatch: 128 };
  const model = { id: 'fixture-q6', name: 'Fixture Q6', bytes: 1073741824, available: true,
    engines: ['kmic'], quantization: 'Q6_K', sha256: 'b'.repeat(64), engine_version: 'build-1',
    gpu: 'Tesla P100', gpu_count: 2, limits: settings, initial: { ...settings, context: 4096 } };
  const controlToken = 'engine-control-'.repeat(4);
  let presets: object[] = [];
  let config: object | undefined;
  const stages: object[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const key = request.url().split('/').pop()!;
    if (key === 'model-presets') {
      expect(request.headers()['x-lab-engine-token']).toBeUndefined();
      const body = request.postDataJSON();
      expect(body).toEqual({ model_id: model.id, name: 'Chat', workload: '', settings, make_default: true, evidence: null });
      const preset = { ...body, id: 1, is_default: true, environment: model };
      presets = [preset];
      return route.fulfill({ status: 201, json: preset });
    }
    if (key === 'selection') {
      expect(request.headers().authorization).toBe('Bearer ' + token);
      expect(request.headers()['x-lab-engine-token']).toBe(controlToken);
      stages.push(request.postDataJSON());
      config = { model_id: model.id, settings };
      return route.fulfill({ json: { accepted: true } });
    }
    const kmic = { id: 'kmic', unit: 'kmic-qwen27b.service', state: 'inactive', ready: false, model: null,
      controls_available: true, can_stage: true, boot_enabled: true, catalog: [model],
      configured_model: config ? model.name : null, configured_selection: config };
    await route.fulfill({ json: key === 'models' ? { ...(fixture.models as object), presets }
      : key === 'engines' ? { controls_enabled: true, items: [kmic] } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const library = page.getByRole('region', { name: 'Model library' });
  const engines = page.getByRole('region', { name: 'Engine services' });
  const engine = library.getByRole('combobox', { name: 'Engine', exact: true });
  const approved = library.getByRole('combobox', { name: 'Approved model' });
  await engine.selectOption('pxa');
  await expect(approved).toBeDisabled();
  await engine.selectOption('kmic');
  await approved.selectOption(model.id);
  await expect(library.getByRole('button', { name: 'Configure next start' })).toBeDisabled();
  await library.getByLabel('Context tokens').fill('8192');
  await library.getByLabel('Preset name').fill('Chat');
  await library.getByLabel('Make default for this model').check();
  await library.getByRole('button', { name: 'Save preset' }).click();
  await expect(library.getByText('Preset saved.')).toBeVisible();
  await expect(library.getByRole('combobox', { name: 'Load preset' })).toHaveValue('1');
  await approved.selectOption('');
  await approved.selectOption(model.id);
  await expect(library.getByLabel('Context tokens')).toHaveValue('8192');
  await engines.getByLabel('Engine control token').fill(controlToken);
  await engines.getByRole('button', { name: 'Unlock controls' }).click();
  await library.getByRole('button', { name: 'Configure next start' }).click();
  expect(stages).toHaveLength(0);
  await library.getByRole('button', { name: 'Confirm next-start configuration' }).click();
  await expect(library.getByText('Configuration saved for the next Kmic launch.', { exact: false })).toBeVisible();
  expect(stages).toEqual([{ model_id: model.id, settings, confirmed: true }]);
  await expect(engines.getByText('8192 context · 512 batch · 128 microbatch')).toBeVisible();
  await expect(engines.getByText('Not reported', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('reviewed fast and reasoning profiles export distinct client settings without credentials or host paths', async ({ page }) => {
  const settings = { context: 32768, batch: 1024, ubatch: 512 };
  const catalog = ['Fast', 'Reasoning'].map(mode => ({
    id: 'flash-next-' + mode.toLowerCase(), name: 'Flash Next ' + mode, bytes: 83626438426,
    available: true, engines: ['kmic'], quantization: 'UD-IQ3_XXS', sha256: 'b'.repeat(64),
    engine_version: 'tested-build', gpu: 'Tesla P100', gpu_count: 2, initial: settings, limits: settings,
    recipe: { id: 'flash-next-' + mode.toLowerCase() + '-v1', mode, expert_cache_mib: 20480,
      tensor_split: [55, 45], draft_tokens: 2, served_alias: 'flash-next-' + mode.toLowerCase(),
      client_settings: { temperature: mode === 'Fast' ? 0.7 : 1, top_p: mode === 'Fast' ? 0.8 : 0.95,
        chat_template_kwargs: { enable_thinking: mode === 'Reasoning' },
        reasoning_budget_tokens: mode === 'Fast' ? 0 : 1024 }, notice: 'Small coding smoke test.' },
  }));
  await page.route('**/api/**', route => {
    const key = route.request().url().split('/').pop()!;
    return route.fulfill({ json: key === 'engines' ? { controls_enabled: true, items: [{
      id: 'kmic', state: 'inactive', controls_available: true, can_stage: true, catalog,
    }] } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const library = page.getByRole('region', { name: 'Model library' });
  for (const mode of ['Fast', 'Reasoning']) {
    await library.getByLabel('Approved model').selectOption('flash-next-' + mode.toLowerCase());
    await expect(library.getByLabel('Context tokens')).toHaveAttribute('readonly', '');
    await expect(library.getByRole('heading', { name: mode + ' coding profile' })).toBeVisible();
    const downloadEvent = page.waitForEvent('download');
    await library.getByRole('button', { name: 'Download Oh My Pi preset' }).click();
    const download = await downloadEvent;
    const raw = await readFile((await download.path())!, 'utf8');
    const config = JSON.parse(raw);
    const provider = config.providers['p100-' + mode.toLowerCase()];
    expect(provider.apiKey).toBe('REPLACE_WITH_INFERENCE_API_KEY');
    expect(provider.models[0].id).toBe('flash-next-' + mode.toLowerCase());
    expect(provider.models[0].reasoning).toBe(mode === 'Reasoning');
    expect(provider.models[0].compat.extraBody.chat_template_kwargs.enable_thinking).toBe(mode === 'Reasoning');
    expect(raw).not.toContain(token);
    expect(raw).not.toContain('/home/trav');
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await library.getByText('Client request settings', { exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/flash-next-profiles-mobile.png', fullPage: true });
});
