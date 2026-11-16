import { test, expect } from "@playwright/test";
import { fixture, token } from "./fixtures";

const id = "33333333-3333-4333-8333-333333333333";
const nativeJob = {
  id,
  source: "native",
  status: "succeeded",
  created_at: "2026-11-16T00:00:00Z",
  error_code: null,
  preset_id: "approved-test",
  provenance: { engine: "kmic", model_id: "approved-model" },
  summary: {
    sample_count: 2,
    metrics: {
      prefill_tokens_per_second: { median: 128 },
      decode_tokens_per_second: { median: 16 },
    },
  },
};
const detail = {
  ...nativeJob,
  attempts: [
    {
      phase: "measured",
      trial: 0,
      sample: {
        prefill_tokens_per_second: 128,
        decode_tokens_per_second: 16,
        process_wall_ms: 50,
      },
    },
  ],
};

test("native history defaults disconnected and exposes no execution controls", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      json: fixture[new URL(route.request().url()).pathname.split("/").pop()!],
    }),
  );
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const section = page.locator("#native-benchmarks");
  await expect(
    section.getByText("Native history is not connected.", { exact: false }),
  ).toBeVisible();
  await expect(section.getByRole("button")).toHaveCount(0);
});

test("authenticated native history and export remain separate from simulated fixtures", async ({
  page,
}) => {
  let mutations = 0;
  await page.route("**/api/**", (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    expect(request.headers().authorization).toBe("Bearer " + token);
    if (request.method() !== "GET") mutations++;
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
          items: [nativeJob],
        },
      });
    return route.fulfill({ json: fixture[path.split("/").pop()!] });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const section = page.locator("#native-benchmarks");
  await section.getByRole("button", { name: "View native results" }).click();
  await expect(
    section.getByText("Median prefill:", { exact: false }),
  ).toContainText("128.00");
  await expect(
    section.getByText("CLI token microbenchmark;", { exact: false }),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await section.getByRole("button", { name: "Export native JSON" }).click();
  expect((await download).suggestedFilename()).toBe(
    `native-benchmark-${id}.json`,
  );
  expect(mutations).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
