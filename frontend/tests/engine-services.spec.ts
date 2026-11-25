import { test, expect } from '@playwright/test';
import { fixture, token } from './fixtures';

const controlToken = 'engine-control-'.repeat(4);
const kmic = { id: 'kmic', unit: 'kmic-qwen27b.service', state: 'active', ready: true,
  note: 'Live service status', model: 'qwen3.8-27b-p100', configured_model: 'Qwen3.8 27B Q6_K',
  boot_enabled: true, boot_state: 'enabled', controls_available: true };

test('Kmic status, separate token, explicit stop/boot confirmation and token clearing', async ({ page }) => {
  const submitted: string[] = [];
  let denied = false;
  await page.route('**/api/**', async route => {
    expect(route.request().headers().authorization).toBe('Bearer ' + token);
    const key = route.request().url().split('/').pop()!;
    if (key === 'control') {
      expect(route.request().headers()['x-lab-engine-token']).toBe(controlToken);
      submitted.push(route.request().postDataJSON().action);
      return route.fulfill({ status: denied ? 403 : 200, json: denied ? {} : { accepted: true } });
    }
    await route.fulfill({ json: key === 'engines' ? { controls_enabled: true,
      items: [kmic, ...(fixture.engines as { items: object[] }).items.slice(1)] } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const engines = page.getByRole('region', { name: 'Engine services', exact: true });
  await expect(engines.getByText('qwen3.8-27b-p100', { exact: true })).toBeVisible();
  await expect(engines.getByText('Ready', { exact: true })).toBeVisible();
  await expect(engines.getByRole('button', { name: 'stop', exact: true }).first()).toBeDisabled();
  await engines.getByLabel('Engine control token').fill(controlToken);
  await engines.getByRole('button', { name: 'Unlock controls', exact: true }).click();
  await engines.screenshot({ path: 'test-results/engine-services.png' });
  await engines.getByRole('button', { name: 'stop', exact: true }).first().click();
  await expect(engines.getByText('This will interrupt', { exact: false })).toBeVisible();
  expect(submitted).toEqual([]);
  await engines.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(submitted).toEqual([]);
  await engines.getByRole('button', { name: 'Disable automatic startup', exact: true }).click();
  await engines.getByRole('button', { name: 'Confirm disable boot', exact: true }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted).toEqual(['disable_boot']);
  await engines.getByRole('button', { name: 'stop', exact: true }).first().click();
  denied = true;
  await engines.getByRole('button', { name: 'Confirm stop', exact: true }).click();
  await expect(engines.getByText('Control access denied.', { exact: false })).toBeVisible();
  await expect(engines.getByLabel('Engine control token')).toHaveValue('');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.getByRole('button', { name: 'Disconnect & clear token' }).click();
  await expect(page.getByLabel('Access token', { exact: true })).toHaveValue('');
});

test('stale engine data prevents control actions', async ({ page }) => {
  await page.clock.install();
  let fail = false;
  await page.route('**/api/**', async route => {
    expect(route.request().method()).toBe('GET');
    const key = route.request().url().split('/').pop()!;
    if (fail && key === 'engines') return route.fulfill({ status: 503, json: {} });
    await route.fulfill({ json: key === 'engines' ? { controls_enabled: true, items: [kmic] } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const engines = page.getByRole('region', { name: 'Engine services', exact: true });
  await engines.getByLabel('Engine control token').fill(controlToken);
  await engines.getByRole('button', { name: 'Unlock controls', exact: true }).click();
  await expect(engines.getByRole('button', { name: 'stop', exact: true })).toBeEnabled();
  fail = true;
  await page.clock.fastForward(6000);
  await expect(engines.getByText('Unknown — stale data')).toBeVisible();
  await expect(engines.getByRole('button', { name: 'stop', exact: true })).toBeDisabled();
});

test('Strata uses the shared controls after Kmic stops', async ({ page }) => {
  let kmicState = 'active';
  const submitted: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/control')) {
      expect(route.request().headers()['x-lab-engine-token']).toBe(controlToken);
      submitted.push(path);
      if (path === '/api/engines/kmic/control') kmicState = 'inactive';
      return route.fulfill({ json: { accepted: true } });
    }
    const key = path.split('/').pop()!;
    await route.fulfill({ json: key === 'engines' ? { controls_enabled: true, items: [
      { ...kmic, state: kmicState },
      { id: 'strata', unit: 'p100-lab-strata.service', state: 'inactive', controls_available: true, ready: false },
    ] } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const engines = page.getByRole('region', { name: 'Engine services', exact: true });
  await engines.getByLabel('Engine control token').fill(controlToken);
  await engines.getByRole('button', { name: 'Unlock controls', exact: true }).click();
  const strata = engines.getByRole('article').filter({ has: page.getByRole('heading', { name: 'Strata', exact: true }) });
  const start = strata.getByRole('button', { name: 'start', exact: true });
  await expect(start).toBeDisabled();
  await engines.getByRole('button', { name: 'stop', exact: true }).first().click();
  await engines.getByRole('button', { name: 'Confirm stop', exact: true }).click();
  await expect(start).toBeEnabled();
  await start.click();
  await expect(engines.getByText('Start Strata and load its configured model?')).toBeVisible();
  await engines.getByRole('button', { name: 'Confirm start', exact: true }).click();
  await expect.poll(() => submitted).toEqual(['/api/engines/kmic/control', '/api/engines/strata/control']);
});
