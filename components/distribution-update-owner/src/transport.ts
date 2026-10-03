import {
  createServer,
  type ServerResponse,
  type IncomingMessage,
} from "node:http";
import { createHash, timingSafeEqual, randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { token, type Preferences } from "./types.js";
import { DistributionUpdateOwner, type Receipt } from "./owner.js";

export interface SupervisorConnection {
  url: string;
  token: string;
}
export interface SupervisorNotification {
  cursor: string;
  reset?: boolean;
  receipt?: Receipt;
}
const MAX_BODY = 16384,
  MAX_RESPONSE = 512 * 1024;
function secret(value: string): string {
  if (!/^[a-f0-9]{64}$/.test(value)) throw Error("invalid_supervisor_token");
  return value;
}
function endpoint(value: string): URL {
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw Error("invalid_supervisor_endpoint");
  return url;
}
const digest = (s: string) => createHash("sha256").update(s).digest();
function authorized(request: IncomingMessage, key: string): boolean {
  return (
    !request.headers.origin &&
    request.headers["x-amplifier-supervisor"] === "1" &&
    timingSafeEqual(
      digest(String(request.headers.authorization ?? "")),
      digest("Bearer " + key),
    )
  );
}
async function body(
  request: IncomingMessage,
): Promise<Record<string, unknown>> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw Error("request_limit");
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("invalid_request");
  return value;
}
function keys(
  value: Record<string, unknown>,
  allowed: string[],
  required: string[] = [],
) {
  if (
    Object.keys(value).some((key) => !allowed.includes(key)) ||
    required.some((key) => !Object.hasOwn(value, key))
  )
    throw Error("invalid_request");
}
async function dispatch(
  owner: DistributionUpdateOwner,
  request: Record<string, unknown>,
) {
  keys(request, ["operation", "commandId", "args"], ["operation"]);
  const args = (request.args ?? {}) as Record<string, unknown>;
  if (!args || typeof args !== "object" || Array.isArray(args))
    throw Error("invalid_request");
  const id = () => token(request.commandId);
  switch (request.operation) {
    case "inspect":
      keys(args, []);
      return owner.inspect();
    case "running":
      keys(args, []);
      return owner.inspectRunning();
    case "diagnostics":
      keys(args, []);
      return owner.diagnostics();
    case "receipt":
      keys(args, ["commandId"], ["commandId"]);
      return owner.receipt(token(args.commandId));
    case "restart-proof":
      keys(args, ["commandId"], ["commandId"]);
      return owner.restartProof(token(args.commandId));
    case "reconcile":
      keys(args, ["commandId"], ["commandId"]);
      return owner.reconcile(token(args.commandId));
    case "check":
      keys(args, ["fresh"]);
      return owner.check(
        id(),
        args.fresh === undefined ? true : (args.fresh as boolean),
      );
    case "install":
      keys(args, ["releaseId"]);
      return owner.install(
        id(),
        args.releaseId === undefined || args.releaseId === null
          ? null
          : token(args.releaseId, 100),
      );
    case "rollback":
      keys(args, ["expectedCurrentId"], ["expectedCurrentId"]);
      return owner.rollback(id(), token(args.expectedCurrentId, 100));
    case "preferences":
      keys(
        args,
        ["autoCheck", "autoInstall", "intervalMs"],
        ["autoCheck", "autoInstall", "intervalMs"],
      );
      return owner.setPreferences(id(), args as unknown as Preferences);
    default:
      throw Error("unknown_operation");
  }
}
const publicErrors = new Set([
  "request_limit",
  "invalid_request",
  "invalid_identifier",
  "invalid_fresh",
  "invalid_preferences",
  "unknown_operation",
  "unknown_command",
  "command_identity_conflict",
  "owner_closed",
  "readiness_unconfirmed",
]);
export async function serveSupervisor(options: {
  owner: DistributionUpdateOwner;
  token: string;
  port?: number;
}) {
  const key = secret(options.token),
    epoch = randomUUID();
  let sequence = 0,
    active = 0,
    closed = false;
  const history: SupervisorNotification[] = [],
    followers = new Set<ServerResponse>();
  const json = (response: ServerResponse, status: number, value: unknown) => {
    const output = JSON.stringify(value);
    if (Buffer.byteLength(output) > MAX_RESPONSE) {
      response.writeHead(500, { "Content-Type": "application/json" });
      response.end('{"ok":false,"error":"response_limit"}');
      return;
    }
    response.writeHead(status, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(output);
  };
  const send = (response: ServerResponse, event: SupervisorNotification) => {
    if (
      !response.write(`id: ${event.cursor}\ndata: ${JSON.stringify(event)}\n\n`)
    ) {
      followers.delete(response);
      response.destroy();
    }
  };
  const server = createServer(async (request, response) => {
    if (closed) {
      json(response, 503, { ok: false, error: "supervisor_closed" });
      return;
    }
    if (!authorized(request, key)) {
      json(response, 401, { ok: false, error: "unauthorized" });
      return;
    }
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/v1/events") {
      if (followers.size >= 32) {
        json(response, 429, { ok: false, error: "subscriber_limit" });
        return;
      }
      const cursor = request.headers["last-event-id"];
      if (
        cursor !== undefined &&
        (typeof cursor !== "string" || cursor.length > 100)
      ) {
        json(response, 400, { ok: false, error: "invalid_cursor" });
        return;
      }
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      });
      followers.add(response);
      const index = history.findIndex((event) => event.cursor === cursor);
      if (index >= 0)
        for (const event of history.slice(index + 1)) send(response, event);
      else send(response, { cursor: `${epoch}:${sequence}`, reset: true });
      const heartbeat = setInterval(() => {
        if (!response.write(": keepalive\n\n")) response.destroy();
      }, 15000);
      heartbeat.unref();
      response.on("close", () => {
        clearInterval(heartbeat);
        followers.delete(response);
      });
      return;
    }
    if (request.method !== "POST" || url.pathname !== "/v1/rpc") {
      json(response, 404, { ok: false, error: "not_found" });
      return;
    }
    if (active >= 32) {
      json(response, 429, { ok: false, error: "request_capacity" });
      return;
    }
    if (
      !String(request.headers["content-type"]).startsWith("application/json")
    ) {
      json(response, 415, { ok: false, error: "invalid_content_type" });
      return;
    }
    active++;
    try {
      const input = await body(request);
      const result = await dispatch(options.owner, input);
      json(response, 200, { ok: true, result });
    } catch (error) {
      json(response, 400, {
        ok: false,
        error:
          error instanceof Error && publicErrors.has(error.message)
            ? error.message
            : "operation_failed",
      });
    } finally {
      active--;
    }
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  server.maxConnections = 96;
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? 0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string")
    throw Error("supervisor_listen_failed");
  return {
    url: `http://127.0.0.1:${address.port}/`,
    publish(receipt: Receipt) {
      if (closed) return;
      const event = {
        cursor: `${epoch}:${++sequence}`,
        receipt: structuredClone(receipt),
      };
      if (Buffer.byteLength(JSON.stringify(event)) > 16384) return;
      history.push(event);
      if (history.length > 128) history.shift();
      for (const response of followers) send(response, event);
    },
    async close() {
      closed = true;
      for (const response of followers) response.end();
      server.closeIdleConnections();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    },
  };
}

export class SupervisorClient {
  readonly outlivesDistribution = true as const;
  readonly owner;
  private readonly url: URL;
  private readonly key: string;
  private listeners = new Set<(event: SupervisorNotification) => void>();
  private watch: AbortController | null = null;
  private cursor = "";
  constructor(connection: SupervisorConnection) {
    this.url = endpoint(connection.url);
    this.key = secret(connection.token);
    this.owner = {
      inspect: () => this.rpc("inspect"),
      inspectRunning: () => this.rpc("running"),
      diagnostics: () => this.rpc("diagnostics"),
      check: (id: string, fresh = true) =>
        this.rpc("check", { fresh }, id) as Promise<Receipt>,
      install: (id: string, releaseId: string | null = null) =>
        this.rpc("install", { releaseId }, id) as Promise<Receipt>,
      rollback: (id: string, expectedCurrentId: string) =>
        this.rpc("rollback", { expectedCurrentId }, id) as Promise<Receipt>,
      setPreferences: (id: string, value: Preferences) =>
        this.rpc("preferences", { ...value }, id) as Promise<Receipt>,
      receipt: (commandId: string) =>
        this.rpc("receipt", { commandId }) as Promise<Receipt | null>,
      restartProof: (commandId: string) =>
        this.rpc("restart-proof", { commandId }) as Promise<
          ReturnType<DistributionUpdateOwner["restartProof"]>
        >,
      reconcile: (commandId: string) =>
        this.rpc("reconcile", { commandId }) as Promise<Receipt>,
    };
  }
  private headers() {
    return {
      Authorization: "Bearer " + this.key,
      "X-Amplifier-Supervisor": "1",
    };
  }
  async rpc(
    operation: string,
    args: Record<string, unknown> = {},
    commandId?: string,
  ): Promise<unknown> {
    const uncertain = () =>
      Error(
        commandId || operation === "reconcile"
          ? "supervisor_unreachable_outcome_unknown"
          : "supervisor_unreachable",
      );
    let response: Response;
    try {
      response = await fetch(new URL("v1/rpc", this.url), {
        method: "POST",
        headers: { ...this.headers(), "Content-Type": "application/json" },
        body: JSON.stringify({
          operation,
          args,
          ...(commandId ? { commandId } : {}),
        }),
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      });
    } catch {
      throw uncertain();
    }
    if (!response.body) throw uncertain();
    const reader = response.body.getReader(),
      chunks: Uint8Array[] = [];
    let size = 0;
    let value;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > MAX_RESPONSE) throw Error("supervisor_response_limit");
        chunks.push(chunk.value);
      }
      value = JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
      if (!value || typeof value !== "object" || typeof value.ok !== "boolean")
        throw Error("supervisor_response_invalid");
    } catch {
      // A response can disappear after its headers arrived. That is still an
      // uncertain mutation, not evidence that the accepted command did not run.
      throw uncertain();
    } finally {
      await reader.cancel().catch(() => {});
    }
    if (!response.ok || value.ok !== true)
      throw Error(
        typeof value.error === "string" && /^[a-z_]{1,80}$/.test(value.error)
          ? value.error
          : "supervisor_response_invalid",
      );
    return value.result;
  }
  subscribe(callback: (event: SupervisorNotification) => void): () => void {
    this.listeners.add(callback);
    if (!this.watch) {
      this.watch = new AbortController();
      void this.follow(this.watch);
    }
    return () => {
      this.listeners.delete(callback);
      if (!this.listeners.size) {
        this.watch?.abort();
        this.watch = null;
      }
    };
  }
  close(): void {
    this.listeners.clear();
    this.watch?.abort();
    this.watch = null;
  }
  private async follow(controller: AbortController): Promise<void> {
    let backoff = 250;
    while (!controller.signal.aborted) {
      try {
        const response = await fetch(new URL("v1/events", this.url), {
          headers: {
            ...this.headers(),
            ...(this.cursor ? { "Last-Event-ID": this.cursor } : {}),
          },
          redirect: "error",
          signal: controller.signal,
        });
        if (!response.ok || !response.body)
          throw Error("notification_unavailable");
        const reader = response.body.getReader(),
          decoder = new TextDecoder();
        let buffer = "";
        backoff = 250;
        try {
          while (!controller.signal.aborted) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            if (buffer.length > 65536) throw Error("notification_limit");
            let end;
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const frame = buffer.slice(0, end);
              buffer = buffer.slice(end + 2);
              const data = frame
                .split("\n")
                .find((line) => line.startsWith("data: "));
              if (!data) continue;
              const event = JSON.parse(data.slice(6)) as SupervisorNotification;
              if (typeof event.cursor !== "string" || event.cursor.length > 100)
                throw Error("notification_invalid");
              this.cursor = event.cursor;
              for (const listener of this.listeners)
                try {
                  Promise.resolve(listener(event)).catch(() => {});
                } catch {}
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      } catch {
        /* Reconnect observations only. Mutating RPC calls are never retried. */
      }
      if (!controller.signal.aborted)
        await delay(backoff, undefined, { signal: controller.signal }).catch(
          () => {},
        );
      backoff = Math.min(5000, backoff * 2);
    }
  }
}
