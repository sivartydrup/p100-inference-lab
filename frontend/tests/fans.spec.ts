import { test, expect } from '@playwright/test';
import { fixture, token } from './fixtures';

const fans = {
  available: true, source: 'hwmon', controls_enabled: false, partial: true, truncated: false,
  message: 'Some fan readings are unavailable.',
  devices: [
    { id: 'a', controller: 'it8620', device: 'it87.2624', channel: 'fan1', rpm: 4245 },
    { id: 'b', controller: 'it8620', device: 'it87.2624', channel: 'fan2', rpm: 0 },
    { id: 'c', controller: 'it8792', device: 'it87.2656', channel: 'fan1', rpm: null },
  ],
};

test('fan readings distinguish zero, missing, stale and disappearing channels', async ({ page }) => {
  await page.clock.install();
  let current = fans;
  let failing = false;
  await page.route('**/api/**', async route => {
    expect(route.request().method()).toBe('GET');
    expect(route.request().headers().authorization).toBe('Bearer ' + token);
    const key = route.request().url().split('/').pop()!;
    if (key === 'system' && failing) return route.fulfill({ status: 503, json: {} });
    await route.fulfill({ json: key === 'system' ? { ...(fixture.system as object), fans: current } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token').fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Fan monitoring', exact: true });
  await expect(panel.getByText('4,245 RPM', { exact: true })).toBeVisible();
  await expect(panel.getByText('0 RPM', { exact: true })).toHaveCount(0);
  await expect(panel.getByText('Unavailable', { exact: true })).toBeVisible();
  await expect(panel.getByText('1 zero-RPM input hidden', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: 'Show zero-RPM inputs', exact: true }).click();
  await expect(panel.getByText('0 RPM', { exact: true })).toBeVisible();
  await expect(panel.getByText('No rotation reported', { exact: false })).toBeVisible();
  await panel.getByRole('button', { name: 'Hide zero-RPM inputs', exact: true }).click();
  await expect(panel.getByText('0 RPM', { exact: true })).toHaveCount(0);
  await expect(panel.locator('input,select')).toHaveCount(0);
  await page.screenshot({ path: 'test-results/fan-monitoring-desktop.png', fullPage: true });
  current = { ...fans, devices: fans.devices.map(fan => fan.id === 'b' ? { ...fan, rpm: 4300 } : fan) };
  await page.clock.fastForward(6000);
  await expect(panel.getByText('4,300 RPM', { exact: true })).toBeVisible();
  failing = true;
  await page.clock.fastForward(6000);
  await expect(panel.getByText('STALE READINGS', { exact: true })).toBeVisible();
  failing = false;
  current = { ...fans, available: false, devices: [], message: 'No readable fan RPM sensors are exposed to the dashboard.' };
  await page.clock.fastForward(6000);
  await expect(panel.getByText(current.message)).toBeVisible();
  await expect(panel.getByText('4,245 RPM', { exact: true })).toHaveCount(0);
});

test('mock fan readings are labelled and fit a mobile screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/**', async route => {
    const key = route.request().url().split('/').pop()!;
    await route.fulfill({ json: key === 'system' ? { ...(fixture.system as object), mode: 'mock', fans: { ...fans, source: 'mock' } } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token').fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Fan monitoring', exact: true });
  await expect(panel.getByText('Simulated fan readings', { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/fan-monitoring-mobile.png', fullPage: true });
});

test('fan RPM follows the authenticated stream and clears when sensors disappear', async ({ page }) => {
  await page.clock.install();
  let streams = 0;
  const unavailable = { ...fans, available: false, devices: [], message: 'No readable fan RPM sensors are exposed to the dashboard.' };
  await page.route('**/api/**', async route => {
    expect(route.request().method()).toBe('GET');
    expect(route.request().headers().authorization).toBe('Bearer ' + token);
    const key = route.request().url().split('/').pop()!;
    if (key === 'stream') {
      streams++;
      const system = { ...(fixture.system as object), telemetry_stream: true, sample_sequence: streams,
        sample_age_ms: 0, fans: streams === 1 ? { ...fans, devices: [{ ...fans.devices[0], rpm: 4800 }] } : unavailable };
      return route.fulfill({ contentType: 'text/event-stream', body: 'event: telemetry\ndata: ' + JSON.stringify(system) + '\n\n' });
    }
    await route.fulfill({ json: key === 'system' ? { ...(fixture.system as object), telemetry_stream: true,
      sample_sequence: 1, sample_age_ms: 0, fans } : fixture[key] });
  });
  await page.goto('/');
  await page.getByLabel('Access token').fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Fan monitoring', exact: true });
  await expect(panel.getByText('4,800 RPM', { exact: true })).toBeVisible();
  await page.clock.fastForward(2100);
  await expect(panel.getByText(unavailable.message)).toBeVisible();
  await expect(panel.getByText('4,800 RPM', { exact: true })).toHaveCount(0);
});
