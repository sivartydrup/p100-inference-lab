export const token = "test-token-".repeat(4);
export const fixture: Record<string, unknown> = {
  system: {
    mode: "live",
    scope: "Host",
    cpu_percent: 12,
    memory: { total: 64000000000, used: 16000000000, percent: 25 },
    disk: { percent: 32 },
    gpus: { available: false, devices: [], message: "No GPU installed" },
    sampled_at: "2026-11-15T00:00:00Z",
  },
  "benchmark-jobs": { items: [] },
  "benchmark-presets": {
    items: [
      { id: "fixture-v1", label: "Successful fixture" },
      { id: "fixture-failure-v1", label: "Failure/recovery fixture" },
    ],
    submission_enabled: false,
    real_engines_enabled: false,
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
