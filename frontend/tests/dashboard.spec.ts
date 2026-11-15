import { test, expect } from "@playwright/test";
const token = "test-token-".repeat(4);
const fixture: Record<string, unknown> = {
  system: {
    mode: "live",
    scope: "Host",
    cpu_percent: 12,
    memory: { total: 64000000000, used: 16000000000, percent: 25 },
    disk: { percent: 32 },
    gpus: { available: false, devices: [], message: "No GPU installed" },
    sampled_at: "2026-11-15T00:00:00Z",
  },
  engines: {
    items: ["kmic", "pxa", "qr0n"].map((id) => ({
      id,
      unit: "p100-lab-" + id + ".service",
      state: "inactive",
      note: "Service state only",
    })),
    controls_enabled: false,
  },
  models: {
    items: [{ root: 1, name: "test-model.gguf", bytes: 1073741824 }],
    warnings: [],
    truncated: false,
  },
  benchmarks: {
    items: [
      {
        id: 1,
        engine: "pxa",
        model: "test-model.gguf",
        tokens_per_second: 12.5,
        created_at: "2026-11-15T00:00:00Z",
        source: "manual",
      },
    ],
  },
};
test("authentication, monitoring, disabled controls and token clearing", async ({
  page,
}) => {
  await page.route("**/api/**", async (route) => {
    expect(route.request().headers()["authorization"]).toBe("Bearer " + token);
    await route.fulfill({
      json: fixture[route.request().url().split("/").pop()!],
    });
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Connect to your lab" }),
  ).toBeVisible();
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toBeVisible();
  await expect(
    page.getByText("No GPUs reported.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "start", exact: true }).first(),
  ).toBeDisabled();
  await expect(
    page.getByRole("cell", { name: "test-model.gguf", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        Object.keys(localStorage).length + Object.keys(sessionStorage).length,
    ),
  ).toBe(0);
  await page.screenshot({
    path: "test-results/dashboard-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Disconnect & clear token" }).click();
  await expect(page.getByLabel("Access token")).toHaveValue("");
});
test("authentication failure is visible and mobile layout fits", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 401, json: { detail: "Unauthorized" } }),
  );
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("alert")).toContainText("Authentication failed");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("mock samples are clearly labelled", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const key = route.request().url().split("/").pop()!;
    const value =
      key === "system"
        ? { ...(fixture.system as object), mode: "mock" }
        : fixture[key];
    await route.fulfill({ json: value });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByText("MOCK DEVELOPMENT DATA", { exact: true }),
  ).toBeVisible();
});

test("stalled refresh times out, marks old telemetry stale and recovers", async ({
  page,
}) => {
  await page.clock.install();
  let systemRequests = 0;
  let recover = false;
  await page.route("**/api/**", async (route) => {
    const key = route.request().url().split("/").pop()!;
    if (key === "system") {
      systemRequests++;
      if (systemRequests > 1 && !recover) return; // Leave the request pending.
    }
    await route.fulfill({
      json:
        key === "system" && recover
          ? { ...(fixture.system as object), cpu_percent: 41 }
          : fixture[key],
    });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toBeVisible();
  await page.clock.runFor(5_000);
  await expect.poll(() => systemRequests).toBe(2);
  await page.clock.runFor(10_000);
  await expect(page.getByRole("alert")).toContainText("Refresh timed out");
  await expect(
    page.getByText("STALE / NO LIVE DATA", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByText("12%", { exact: true })).toBeVisible();
  expect(systemRequests).toBe(2);

  recover = true;
  await page.clock.runFor(5_000);
  await expect.poll(() => systemRequests).toBe(3);
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("41%", { exact: true })).toBeVisible();
});

test("sample age expires on returning to a suspended tab", async ({ page }) => {
  await page.clock.install();
  await page.route("**/api/**", (route) =>
    route.fulfill({ json: fixture[route.request().url().split("/").pop()!] }),
  );
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toBeVisible();
  // Advance wall time without running timers, as when a tab/device was suspended.
  await page.clock.setSystemTime(new Date(Date.now() + 60_000));
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(
    page.getByText("STALE / NO LIVE DATA", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "No fresh sample received",
  );
});

test("disconnect stops a stalled poll and prevents further retries", async ({
  page,
}) => {
  await page.clock.install();
  let systemRequests = 0;
  await page.route("**/api/**", (route) => {
    const key = route.request().url().split("/").pop()!;
    if (key === "system" && ++systemRequests > 1) return;
    return route.fulfill({ json: fixture[key] });
  });
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toBeVisible();
  await page.clock.runFor(5_000);
  await expect.poll(() => systemRequests).toBe(2);
  await page.getByRole("button", { name: "Disconnect & clear token" }).click();
  await page.clock.runFor(60_000);
  expect(systemRequests).toBe(2);
  await expect(page.getByLabel("Access token")).toHaveValue("");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("LIVE HOST DATA", { exact: true })).toHaveCount(
    0,
  );
});
