export type StreamSample = {
  telemetry_stream: true;
  sample_sequence: number;
  sample_age_ms: number;
  mode: "live" | "mock";
};

// Fetch permits the existing Authorization header; tokens never enter an SSE URL.
export async function readTelemetry<T>(
  token: string,
  controller: AbortController,
  accept: (sample: T) => void,
) {
  let timer: ReturnType<typeof setTimeout>;
  let lastValid = Date.now();
  const arm = () => {
    clearTimeout(timer);
    lastValid = Date.now();
    timer = setTimeout(() => controller.abort(), 10_000);
  };
  arm();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch("/api/system/stream", {
      headers: { Authorization: "Bearer " + token },
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok || !response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body)
      throw new Error("Telemetry stream unavailable");
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "", sequence = -1;
    while (!controller.signal.aborted) {
      const { done, value } = await reader.read();
      if (Date.now() - lastValid >= 10_000) throw new Error("Telemetry stream expired");
      if (done) throw new Error("Telemetry stream closed");
      pending = (pending + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let end: number;
      while ((end = pending.indexOf("\n\n")) >= 0) {
        if (end > 65_536) throw new Error("Telemetry frame too large");
        const frame = pending.slice(0, end); pending = pending.slice(end + 2);
        if (frame.includes("event: unavailable")) throw new Error("Telemetry unavailable");
        const body = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!body) continue; // Heartbeats never refresh sample age.
        const sample = JSON.parse(body) as StreamSample;
        if (sample.telemetry_stream !== true || !Number.isSafeInteger(sample.sample_sequence) || sample.sample_sequence < 1 ||
            !Number.isFinite(sample.sample_age_ms) || sample.sample_age_ms < 0 || sample.sample_age_ms > 3_000 ||
            !["live", "mock"].includes(sample.mode)) throw new Error("Invalid or stale telemetry");
        if (sample.sample_sequence <= sequence) continue;
        sequence = sample.sample_sequence;
        if (controller.signal.aborted) return;
        accept(sample as T);
        arm();
      }
      if (pending.length > 65_536) throw new Error("Telemetry frame too large");
    }
  } finally {
    clearTimeout(timer!);
    controller.abort();
    if (reader) {
      try { await reader.cancel(); } catch { /* Abort already closed the body. */ }
      reader.releaseLock();
    }
  }
}
