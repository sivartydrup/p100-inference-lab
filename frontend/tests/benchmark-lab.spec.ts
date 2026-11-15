import { test, expect } from "@playwright/test";
import { fixture, token } from "./fixtures";

const id = "11111111-1111-4111-8111-111111111111";
const jobToken = "job-permission-".repeat(4);
const queued = {
  id,
  source: "fixture",
  status: "queued",
  created_at: "2026-11-15T00:00:00Z",
  cancel_requested: false,
  error_code: null,
  summary: null,
  request: { preset: "fixture-v1", trials: 5, warmup: 1, timeout_seconds: 30 },
};
const detail = {
  ...queued,
  status: "succeeded",
  samples: [
    {
      phase: "measured",
      trial: 0,
      ttft_ms: 45,
      prefill_tokens_per_second: 3200,
      decode_tokens_per_second: 160,
    },
  ],
  summary: {
    sample_count: 1,
    metrics: { decode_tokens_per_second: { median: 160, stddev: 0 } },
  },
  events: [{ created_at: queued.created_at, event: "succeeded" }],
};

test("viewer sees fixture history with clear separation and cannot submit", async ({
  page,
}) => {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    expect(route.request().headers().authorization).toBe("Bearer " + token);
    if (path === `/api/benchmark-jobs/${id}`)
      return route.fulfill({ json: detail });
    const key = path.split("/").pop()!;
    return route.fulfill({
      json: key === "benchmark-jobs" ? { items: [detail] } : fixture[key],
    });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByText("SIMULATED / NO INFERENCE", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Fixture submissions are disabled", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Queue simulated job" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "View fixture results" }).click();
  await expect(
    page.getByText("Median synthetic decode:", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Benchmark history", exact: true }),
  ).toBeVisible();
});

test("uncertain submission retries the same ID, cancels a queued job and clears credentials", async ({
  page,
}) => {
  const submissions: { key: string | undefined; body: string | null }[] = [];
  let created = false,
    cancelled = false,
    exports = 0;
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname;
    expect(req.headers().authorization).toBe("Bearer " + token);
    if (path === "/api/benchmark-jobs" && req.method() === "POST") {
      expect(req.headers()["x-lab-job-token"]).toBe(jobToken);
      submissions.push({
        key: req.headers()["idempotency-key"],
        body: req.postData(),
      });
      created = true;
      if (submissions.length === 1) return route.abort("failed");
      return route.fulfill({ json: { job: queued, replayed: true } });
    }
    if (path === `/api/benchmark-jobs/${id}/cancel`) {
      expect(req.headers()["x-lab-job-token"]).toBe(jobToken);
      cancelled = true;
      return route.fulfill({
        json: { ...queued, status: "cancelled", cancel_requested: true },
      });
    }
    if (path === `/api/benchmark-jobs/${id}/export`) {
      exports++;
      return route.fulfill({ json: detail });
    }
    if (path === `/api/benchmark-jobs/${id}`)
      return route.fulfill({
        json: {
          ...queued,
          status: cancelled ? "cancelled" : "queued",
          samples: [],
          events: [],
        },
      });
    const key = path.split("/").pop()!;
    if (key === "benchmark-presets")
      return route.fulfill({
        json: { ...(fixture[key] as object), submission_enabled: true },
      });
    if (key === "benchmark-jobs")
      return route.fulfill({
        json: {
          items: created
            ? [
                {
                  ...queued,
                  status: cancelled ? "cancelled" : "queued",
                  cancel_requested: cancelled,
                },
              ]
            : [],
        },
      });
    return route.fulfill({ json: fixture[key] });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Queue simulated job" }),
  ).toBeDisabled();
  await page.getByLabel("Benchmark job token").fill(jobToken);
  await page.getByRole("button", { name: "Queue simulated job" }).click();
  await expect(
    page.getByRole("button", { name: "Retry fixture submission" }),
  ).toBeVisible();
  await expect(page.getByLabel("Measured trials")).toBeDisabled();
  await page.getByRole("button", { name: "Retry fixture submission" }).click();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  expect(submissions[0].key).toMatch(/^[a-f0-9-]{36}$/);
  await page
    .getByRole("button", { name: "Cancel fixture", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "cancelled", exact: true }),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export fixture JSON" }).click();
  expect((await download).suggestedFilename()).toBe(`fixture-job-${id}.json`);
  expect(exports).toBe(1);
  expect(
    await page.evaluate(
      () =>
        Object.keys(localStorage).length + Object.keys(sessionStorage).length,
    ),
  ).toBe(0);
  await page.getByRole("button", { name: "Disconnect & clear token" }).click();
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByLabel("Benchmark job token")).toHaveValue("");
});

test("a viewer token alone cannot enable job mutations in the UI", async ({
  page,
}) => {
  await page.route("**/api/**", (route) => {
    const key = new URL(route.request().url()).pathname.split("/").pop()!;
    return route.fulfill({
      json:
        key === "benchmark-presets"
          ? { ...(fixture[key] as object), submission_enabled: true }
          : fixture[key],
    });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Queue simulated job" }),
  ).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
