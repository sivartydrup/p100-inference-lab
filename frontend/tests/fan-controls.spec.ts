import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fixture, token } from './fixtures';

const controlKey = 'fan-management-'.repeat(4);
async function setup(page: Page) {
  await page.clock.install();
  const state = { enabled: true, available: true, version: 'a'.repeat(32) + ':0',
    channels: { fan1: { mode: 2, pwm: 70, rpm: 4000 }, fan2: { mode: 2, pwm: 70, rpm: 4000 } } };
  let fail = 0, stale = false;
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const key = request.url().split('/').pop()!;
    if (key === 'fan-controls') {
      expect(request.headers().authorization).toBe('Bearer ' + token);
      if (request.method() === 'POST') {
        expect(request.headers()['x-lab-engine-token']).toBe(controlKey);
        const body = request.postDataJSON(); writes.push(body);
        if (fail) return route.fulfill({ status: fail, json: { detail: 'refused' } });
        expect(body.confirmed).toBe(true);
        const channel = state.channels[body.channel as 'fan1' | 'fan2'];
        if (body.mode !== undefined) channel.mode = { full: 0, auto: 2, manual: 1 }[body.mode as 'full'];
        if (body.pwm !== undefined) channel.pwm = body.pwm;
        state.version = 'a'.repeat(32) + ':' + writes.length;
      }
      return route.fulfill({ json: state });
    }
    if (key === 'system') {
      if (stale) return route.fulfill({ status: 503, json: {} });
      return route.fulfill({ json: { ...(fixture.system as object), fans: {
        available: true, source: 'hwmon', controls_enabled: false, partial: false, truncated: false,
        message: 'Physical assignments unverified.', devices: [
          { id: 'one', controller: 'it8620', device: 'it87.2624', channel: 'fan1', rpm: 4000 },
          { id: 'two', controller: 'it8620', device: 'it87.2624', channel: 'fan2', rpm: 0 },
          { id: 'other', controller: 'it8792', device: 'it87.2656', channel: 'fan1', rpm: 900 },
        ],
      } } });
    }
    return route.fulfill({ json: fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token', { exact: true }).fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Fan monitoring' });
  await expect(panel.getByRole('slider', { name: 'fan1 output' })).toHaveValue('70');
  return { state, panel, writes, setFailure: (value: number) => { fail = value; }, setStale: () => { stale = true; },
    unlock: async () => {
      await panel.getByLabel('Fan control token').fill(controlKey);
      await panel.getByRole('button', { name: 'Unlock fan controls' }).click();
    } };
}

test('two scoped mode switches, manual slider writes and live automatic output', async ({ page }) => {
  const { state, panel, writes, unlock } = await setup(page);
  const modes = panel.getByRole('radiogroup', { name: 'fan1 operating mode' });
  const slider = panel.getByRole('slider', { name: 'fan1 output' });
  await expect(modes.getByRole('radio', { name: 'Auto' })).toBeChecked();
  await expect(modes.getByRole('radio', { name: 'Manual' })).toBeDisabled();
  expect(await panel.getByRole('radiogroup').count()).toBe(2);
  await expect(panel.getByText('0 RPM', { exact: true })).toBeVisible();
  await unlock();
  await expect(slider).toBeDisabled();
  await modes.getByRole('radio', { name: 'Manual' }).click();
  await expect(slider).toBeEnabled();
  expect(writes[0]).toEqual({ channel: 'fan1', mode: 'manual', version: 'a'.repeat(32) + ':0', confirmed: true });
  await slider.focus(); await slider.press('End');
  await expect(slider).toHaveValue('255');
  await expect(panel.getByText('Fan setting verified by the host.')).toBeVisible();
  expect(writes[1]).toEqual({ channel: 'fan1', pwm: 255, version: 'a'.repeat(32) + ':1', confirmed: true });
  await modes.getByRole('radio', { name: 'Full' }).click();
  await expect(slider).toBeDisabled();
  await modes.getByRole('radio', { name: 'Auto' }).click();
  state.channels.fan1.pwm = 90;
  await page.clock.fastForward(3000);
  await expect(slider).toHaveValue('90');
  await expect(slider).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/fan-controls-mobile.png', fullPage: true });
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('zero output asks for confirmation; state conflicts do not retry writes', async ({ page }) => {
  const { panel, writes, unlock, setFailure } = await setup(page);
  await unlock();
  const slider = panel.getByRole('slider', { name: 'fan1 output' });
  await panel.getByRole('radiogroup', { name: 'fan1 operating mode' }).getByRole('radio', { name: 'Manual' }).click();
  await expect(slider).toBeEnabled();
  page.once('dialog', dialog => dialog.dismiss());
  await slider.press('Home');
  await expect(slider).toHaveValue('70');
  expect(writes).toHaveLength(1);
  setFailure(409);
  await slider.press('End');
  await expect(panel.getByText('Fan change refused or outcome unknown.', { exact: false })).toBeVisible();
  await page.clock.fastForward(4000);
  expect(writes).toHaveLength(2);
  await expect(slider).toHaveValue('70');
});

test('stale telemetry blocks fan changes and forbidden writes clear the credential', async ({ page }) => {
  const { panel, writes, unlock, setFailure, setStale } = await setup(page);
  await unlock(); setFailure(403);
  const full = panel.getByRole('radiogroup', { name: 'fan1 operating mode' }).getByRole('radio', { name: 'Full' });
  await full.click();
  await expect(panel.getByLabel('Fan control token')).toHaveValue('');
  setFailure(0); await unlock(); setStale();
  await page.clock.fastForward(6000);
  await expect(full).toBeDisabled();
  expect(writes).toHaveLength(1);
});
