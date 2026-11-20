import { test, expect } from '@playwright/test';
import { token, fixture } from './fixtures';

test('stream parser handles chunk boundaries, ignores repeats and bounds frames', async ({ page }) => {
  await page.goto('/');
  const result = await page.evaluate(async () => {
    const modulePath = '/src/telemetryStream.ts';
    const { readTelemetry } = await import(modulePath);
    const original = window.fetch;
    const run = async (chunks: string[]) => {
      window.fetch = async () => new Response(new ReadableStream({ start(controller) {
        chunks.forEach(chunk => controller.enqueue(new TextEncoder().encode(chunk)));
        controller.close();
      } }), { headers: { 'Content-Type': 'text/event-stream' } });
      const accepted: number[] = [];
      const controller = new AbortController();
      try { await readTelemetry('memory-only', controller, (sample: { sample_sequence: number }) => accepted.push(sample.sample_sequence)); }
      catch { /* EOF and malformed frames intentionally terminate the stream. */ }
      return { accepted, aborted: controller.signal.aborted };
    };
    const frame = (sequence: number) => 'data: ' + JSON.stringify({ mode: 'live', telemetry_stream: true,
      sample_sequence: sequence, sample_age_ms: 0 }) + '\r\n\r\n';
    try {
      const body = frame(1) + frame(1) + ': heartbeat\r\n\r\n' + frame(2);
      return [await run([...body]), await run(['data: ' + 'x'.repeat(65_537)]),
        await run(['event: unavailable\ndata: {}\n\n'])];
    } finally { window.fetch = original; }
  });
  expect(result).toEqual([{ accepted: [1, 2], aborted: true },
    { accepted: [], aborted: true }, { accepted: [], aborted: true }]);
});

for (const stale of [false, true]) {
  test(`authenticated stream ${stale ? 'rejects stale frames and falls back' : 'updates telemetry and clears on disconnect'}`, async ({ page }) => {
    let streams = 0;
    await page.route('**/api/**', async route => {
      expect(route.request().headers().authorization).toBe('Bearer ' + token);
      expect(route.request().url()).not.toContain(token);
      const key = route.request().url().split('/').pop()!;
      if (key === 'stream') {
        streams++;
        const system = { ...(fixture.system as object), telemetry_stream: true,
          sample_sequence: 2, sample_age_ms: stale ? 30_000 : 0, cpu_percent: 42 };
        await route.fulfill({ contentType: 'text/event-stream', body: `: heartbeat\n\nevent: telemetry\ndata: ${JSON.stringify(system)}\n\n` });
      } else {
        await route.fulfill({ json: key === 'system'
          ? { ...(fixture.system as object), telemetry_stream: true, sample_sequence: 1, sample_age_ms: 0 }
          : fixture[key] });
      }
    });
    await page.goto('/');
    await page.getByLabel('Access token').fill(token);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await expect(page.getByLabel('Telemetry connection')).toContainText('polling every 5 seconds');
    expect(streams).toBeGreaterThan(0);
    await expect(page.getByText(stale ? '12%' : '42%', { exact: true })).toBeVisible();
    if (stale) await expect(page.getByText('42%', { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Disconnect & clear token' }).click();
    const stopped = streams;
    await page.waitForTimeout(2_200);
    expect(streams).toBe(stopped);
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  });
}
