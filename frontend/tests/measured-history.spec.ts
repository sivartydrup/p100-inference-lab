import { test, expect } from "@playwright/test";
import { fixture, token } from "./fixtures";

const id = "44444444-4444-4444-8444-444444444444";
const imported = {
  id,
  source: "native",
  status: "failed",
  created_at: "2026-01-01T00:00:00Z",
  error_code: "temperature_cutoff",
  preset_id: "import-test",
  provenance: {
    engine: "kmic",
    model_id: "synthetic-model",
    record_origin: "operator_import",
    comparison_key: "same-model-and-workload",
    observed_seconds: 306.3,
    target_seconds: 600,
    declared_power_limit_w: 250,
    power_limit_evidence: "operator_declared",
    gpu_name: "Synthetic test GPU",
    driver_version: "123.45",
    workload: { prompt_tokens: 512, output_tokens: 256, context: 2048 },
  },
  summary: {
    sample_count: 2,
    partial: true,
    metrics: {
      prefill_tokens_per_second: { median: 512 },
      decode_tokens_per_second: { median: 191.25 },
      first_text_ms: { median: 1100 },
      request_wall_ms: { median: 2500 },
    },
    telemetry: {
      peak_temperature_c: 75,
      mean_power_w: 190,
      peak_power_w: 210,
      sampled_peak_vram_mib: 4000,
      measured_samples: 2,
    },
  },
};
const detail = {
  ...imported,
  attempts: [
    {
      phase: "warmup",
      trial: 0,
      sample: {
        prefill_tokens_per_second: 512,
        decode_tokens_per_second: 255,
        first_text_ms: 1000,
        request_wall_ms: 2000,
      },
    },
    {
      phase: "measured",
      trial: 0,
      sample: {
        prefill_tokens_per_second: 512,
        decode_tokens_per_second: 191.25,
        first_text_ms: 1100,
        request_wall_ms: 2500,
      },
    },
    {
      phase: "telemetry",
      trial: 0,
      sample: {
        timestamp: "2026-01-01T00:05:06Z",
        temperature_c: 75,
        power_w: 210,
        elapsed_measured_seconds: 306.3,
      },
    },
  ],
};

test("imported measurements compare cap evidence, partial windows and streamed text without execution controls", async ({
  page,
}) => {
  let mutations = 0;
  const peer = {
    ...imported,
    id: "55555555-5555-4555-8555-555555555555",
    status: "succeeded",
    error_code: null,
    provenance: {
      ...imported.provenance,
      engine: "qr0n",
      declared_power_limit_w: 150,
      power_limit_evidence: "sampled",
      observed_seconds: 600,
    },
    summary: { ...imported.summary, partial: false },
  };
  await page.route("**/api/**", (route) => {
    const r = route.request(),
      path = new URL(r.url()).pathname;
    expect(r.headers().authorization).toBe("Bearer " + token);
    if (r.method() !== "GET") mutations++;
    if (
      path === `/api/native-benchmarks/${id}` ||
      path === `/api/native-benchmarks/${id}/export`
    )
      return route.fulfill({ json: detail });
    if (path === "/api/native-benchmarks")
      return route.fulfill({
        json: {
          configured: true,
          execution_available_via_api: false,
          items: [imported, peer],
        },
      });
    return route.fulfill({ json: fixture[path.split("/").pop()!] });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const section = page.locator("#native-benchmarks"),
    comparison = section.getByLabel("Measured run comparison");
  await expect(comparison.getByText("250 W", { exact: false })).toContainText(
    "declared",
  );
  await expect(comparison.getByText("150 W", { exact: false })).toContainText(
    "logged",
  );
  await expect(
    comparison.getByText("Partial / temperature_cutoff"),
  ).toBeVisible();
  await section
    .getByRole("button", { name: "View native results" })
    .first()
    .click();
  const panel = section.getByLabel("Imported measurement details");
  await expect(panel.getByRole("alert")).toContainText("did not complete");
  await expect(
    panel.getByText("Median prefill:", { exact: false }),
  ).toContainText("191.25");
  await expect(
    panel.getByText("not exact raw-token TTFT", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("Recent telemetry")).toBeVisible();
  const download = page.waitForEvent("download");
  await section.getByRole("button", { name: "Export native JSON" }).click();
  expect((await download).suggestedFilename()).toBe(
    `native-benchmark-${id}.json`,
  );
  await expect(
    section.getByRole("button", { name: /start|cancel|import/i }),
  ).toHaveCount(0);
  expect(mutations).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
