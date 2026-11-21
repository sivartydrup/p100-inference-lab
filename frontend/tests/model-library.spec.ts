import { expect, test } from '@playwright/test';
import { fixture, token } from './fixtures';

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
