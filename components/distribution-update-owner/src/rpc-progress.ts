import type {ServerResponse} from "node:http";

// A release can verify several runtimes and settle many independent owners.
// The former ten-second *total* RPC deadline turned healthy work into an
// unknown outcome while the host kept releasing. Bound network silence, not
// the aggregate operation. Keepalives carry no readiness or settlement proof.
export const RPC_PROGRESS_HEADER = "x-amplifier-rpc-progress";

/** Opt-in JSON whitespace keeps the existing envelope readable by old parsers.
 * Call only after authentication and request parsing. Old clients retain the
 * non-streamed response/status behavior. Owner-side deadlines remain intact. */
export function keepRpcResponseAlive(response: ServerResponse, intervalMs = 1000): () => void {
  const pulse = () => {
    if (response.destroyed || response.writableEnded) return stop();
    if (!response.headersSent) response.writeHead(200, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    if (!response.write("\n")) response.destroy();
  };
  const timer = setInterval(pulse, intervalMs);
  timer.unref();
  const stop = () => { clearInterval(timer); response.off("close", stop); };
  response.once("close", stop);
  return stop;
}

/** One authenticated RPC, never retried. A lost/truncated/silent response is
 * still an unknown effect at the caller. Leading keepalive whitespace is not
 * accumulated; the final JSON envelope retains its strict byte bound. */
export async function fetchRpcJson(url: URL, init: RequestInit, maxBytes: number, idleMs = 10000): Promise<{response: Response; value: unknown}> {
  const idle = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const reset = () => {
    clearTimeout(timer);
    timer = setTimeout(() => idle.abort(), idleMs);
    timer.unref();
  };
  reset();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch(url, {
      ...init,
      headers: {...Object.fromEntries(new Headers(init.headers)), [RPC_PROGRESS_HEADER]: "1"},
      signal: init.signal ? AbortSignal.any([init.signal, idle.signal]) : idle.signal,
    });
    reset();
    if (!response.body) throw Error("rpc_response_missing");
    reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0, started = false;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      reset();
      let bytes = chunk.value;
      if (!started) {
        let i = 0;
        while (i < bytes.length && [9, 10, 13, 32].includes(bytes[i])) i++;
        bytes = bytes.subarray(i);
        if (!bytes.length) continue;
        started = true;
      }
      size += bytes.length;
      if (size > maxBytes) throw Error("rpc_response_limit");
      chunks.push(bytes);
    }
    return {response, value: JSON.parse(Buffer.concat(chunks, size).toString("utf8"))};
  } finally {
    clearTimeout(timer!);
    await reader?.cancel().catch(() => {});
  }
}
