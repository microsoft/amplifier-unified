import type {
  ReleaseNotesQuery,
  ReleaseNotesPage,
  NoticeReview,
} from "./release-notes.js";
import {
  createServer,
  type ServerResponse,
  type IncomingMessage,
} from "node:http";
import { createHash, timingSafeEqual, randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { token, type Preferences } from "./types.js";
import { DistributionUpdateOwner, type Receipt } from "./owner.js";
import type {PreferencesResetPort, PreferencesResetReceipt} from "./app-reset.js";
import type { ServiceLifecycleOwner } from "./service-owner.js";
import {
  serviceIdentity,
  type ServiceCommand,
  type ServiceReceipt,
} from "./service-types.js";

export interface SupervisorConnection {
  url: string;
  token: string;
}
export interface SupervisorConnectionSource {
  /** Resolve once per RPC or observation reconnect; never retry a mutation. */
  connect(): SupervisorConnection | Promise<SupervisorConnection>;
  /** Optional local discovery notifications. These only reconnect observations. */
  onChange?(notify: () => void): () => void;
}
export interface SupervisorNotification {
  cursor: string;
  reset?: boolean;
  receipt?: Receipt;
  serviceReceipt?: ServiceReceipt;
  appResetReceipt?: PreferencesResetReceipt;
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
  service?: ServiceLifecycleOwner,
) {
  keys(request, ["operation", "commandId", "args"], ["operation"]);
  const args = (request.args ?? {}) as Record<string, unknown>;
  if (!args || typeof args !== "object" || Array.isArray(args))
    throw Error("invalid_request");
  const id = () => token(request.commandId);
  if (String(request.operation).startsWith("service-")) {
    if (!service) throw Error("service_not_configured");
    switch (request.operation) {
      case "service-inspect":
        keys(args, []);
        return service.inspect();
      case "service-receipt":
      case "service-proof":
        keys(args, ["commandId"], ["commandId"]);
        return service.receipt(token(args.commandId));
      case "service-reconcile":
        keys(args, ["commandId"], ["commandId"]);
        return service.reconcile(token(args.commandId));
      case "service-stop":
      case "service-adopt":
        keys(args, ["expected"], ["expected"]);
        return request.operation === "service-stop"
          ? service.stop({
              commandId: id(),
              expected: serviceIdentity(args.expected as any),
            })
          : service.adopt({
              commandId: id(),
              expected: serviceIdentity(args.expected as any),
            });
      case "service-resume":
        keys(
          args,
          ["expected", "stoppedCommandId"],
          ["expected", "stoppedCommandId"],
        );
        return service.resume({
          commandId: id(),
          expected: serviceIdentity(args.expected as any),
          stoppedCommandId: token(args.stoppedCommandId),
        });
      default:
        throw Error("unknown_operation");
    }
  }
  switch (request.operation) {
    case "app-reset":
      keys(args,["operation","args","fence"],["operation","args"]);
      if(!["prepare","apply","restore","inspect"].includes(String(args.operation)) ||
        (args.operation!=="inspect" && (args.args as any)?.commandId!==id()))throw Error("invalid_request");
      return owner.appReset.perform(String(args.operation),args.args as Record<string,unknown>,args.fence);
    case "inspect":
      keys(args, []);
      return owner.inspect();
    case "release-notes":
      keys(args, ["cursor", "limit"]);
      return owner.releaseNotes(args as ReleaseNotesQuery);
    case "review-notice":
      keys(
        args,
        ["version", "noticeId", "contentDigest"],
        ["version", "noticeId", "contentDigest"],
      );
      return owner.reviewNotice(id(), args as unknown as NoticeReview);
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
    case "prepare":
    case "install":
      keys(args, ["releaseId"]);
      return owner[request.operation as "prepare" | "install"](
        id(),
        args.releaseId === undefined || args.releaseId === null
          ? null
          : token(args.releaseId, 100),
      );
    case "activate":
      keys(args, ["preparedCommandId", "targetDigest", "expectedCurrentId"], ["preparedCommandId", "targetDigest", "expectedCurrentId"]);
      return owner.activate(id(), args as {preparedCommandId: string; targetDigest: string; expectedCurrentId: string | null});
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
  "service_not_configured",
  "service_owner_binding_conflict",
  "invalid_service_identity",
  "request_limit",
  "invalid_request",
  "invalid_identifier",
  "invalid_fresh",
  "invalid_release_proof",
  "invalid_preferences",
  "release_notes_query_invalid",
  "release_notes_cursor_stale",
  "release_notes_invalid",
  "invalid_notice_review",
  "unknown_operation",
  "unknown_command",
  "command_identity_conflict",
  "owner_closed",
  "preferences_reset_refused",
  "preferences_reset_busy",
  "readiness_unconfirmed",
]);
export async function serveSupervisor(options: {
  owner: DistributionUpdateOwner;
  service?: ServiceLifecycleOwner;
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
      const result = await dispatch(options.owner, input, options.service);
      json(response, 200, { ok: true, result });
    } catch (error) {
      json(response, 400, {
        ok: false,
        ...((error as any)?.data?.executed===false ? {data:{executed:false}} : {}),
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
    publishAppReset(receipt: PreferencesResetReceipt) {
      if(closed)return;
      const event={cursor:`${epoch}:${++sequence}`,appResetReceipt:structuredClone(receipt)};
      if(Buffer.byteLength(JSON.stringify(event))>16384)return;
      history.push(event);
      if(history.length>128)history.shift();
      for(const response of followers)send(response,event);
    },
    publishService(receipt: ServiceReceipt) {
      if (closed) return;
      const event = {
        cursor: `${epoch}:${++sequence}`,
        serviceReceipt: structuredClone(receipt),
      };
      if (Buffer.byteLength(JSON.stringify(event)) > 16384) return;
      history.push(event);
      if (history.length > 128) history.shift();
      for (const response of followers) send(response, event);
    },
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
  readonly service;
  readonly appReset: PreferencesResetPort;
  private readonly source: SupervisorConnectionSource;
  private listeners = new Set<(event: SupervisorNotification) => void>();
  private watch: AbortController | null = null;
  private unwatchConnection: (() => void) | null = null;
  private cursor = "";
  constructor(connection: SupervisorConnection | SupervisorConnectionSource) {
    if ("connect" in connection) this.source = connection;
    else {
      const fixed = {
        url: endpoint(connection.url).href,
        token: secret(connection.token),
      };
      this.source = { connect: () => fixed };
    }
    this.owner = {
      inspect: () => this.rpc("inspect"),
      releaseNotes: (query: ReleaseNotesQuery = {}) =>
        this.rpc("release-notes", { ...query }) as Promise<ReleaseNotesPage>,
      reviewNotice: (id: string, review: NoticeReview) =>
        this.rpc("review-notice", { ...review }, id) as Promise<Receipt>,
      inspectRunning: () => this.rpc("running"),
      diagnostics: () => this.rpc("diagnostics"),
      check: (id: string, fresh = true) =>
        this.rpc("check", { fresh }, id) as Promise<Receipt>,
      install: (id: string, releaseId: string | null = null) =>
        this.rpc("install", { releaseId }, id) as Promise<Receipt>,
      prepare: (id: string, releaseId: string | null = null) =>
        this.rpc("prepare", { releaseId }, id) as Promise<Receipt>,
      activate: (id: string, value: {preparedCommandId: string; targetDigest: string; expectedCurrentId: string | null}) =>
        this.rpc("activate", { ...value }, id) as Promise<Receipt>,
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
    this.appReset={id:"updates",parts:["updates.preferences"],perform:(operation,args,fence,_context)=>
      this.rpc("app-reset",{operation,args,...(fence===undefined?{}:{fence})},operation==="inspect"?undefined:token(args.commandId)) as Promise<Record<string,any>>};
    this.service = {
      inspect: () =>
        this.rpc("service-inspect") as ReturnType<
          ServiceLifecycleOwner["inspect"]
        >,
      stop: (command: ServiceCommand) =>
        this.rpc(
          "service-stop",
          { expected: serviceIdentity(command.expected) },
          command.commandId,
        ) as Promise<ServiceReceipt>,
      resume: (command: ServiceCommand & { stoppedCommandId: string }) =>
        this.rpc(
          "service-resume",
          {
            expected: serviceIdentity(command.expected),
            stoppedCommandId: token(command.stoppedCommandId),
          },
          command.commandId,
        ) as Promise<ServiceReceipt>,
      adopt: (command: ServiceCommand) =>
        this.rpc(
          "service-adopt",
          { expected: serviceIdentity(command.expected) },
          command.commandId,
        ) as Promise<ServiceReceipt>,
      receipt: (commandId: string) =>
        this.rpc("service-receipt", {
          commandId: token(commandId),
        }) as Promise<ServiceReceipt | null>,
      proof: (commandId: string) =>
        this.rpc("service-proof", {
          commandId: token(commandId),
        }) as Promise<ServiceReceipt | null>,
      reconcile: (commandId: string) =>
        this.rpc("service-reconcile", {
          commandId: token(commandId),
        }) as Promise<ServiceReceipt>,
    };
  }
  private async connection() {
    const value = await this.source.connect();
    return { url: endpoint(value.url), key: secret(value.token) };
  }
  private headers(key: string) {
    return {
      Authorization: "Bearer " + key,
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
        commandId ||
          operation === "reconcile" ||
          operation === "service-reconcile"
          ? "supervisor_unreachable_outcome_unknown"
          : "supervisor_unreachable",
      );
    let response: Response;
    try {
      const connection = await this.connection();
      response = await fetch(new URL("v1/rpc", connection.url), {
        method: "POST",
        headers: {
          ...this.headers(connection.key),
          "Content-Type": "application/json",
        },
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
      throw Object.assign(Error(
        typeof value.error === "string" && /^[a-z_]{1,80}$/.test(value.error)
          ? value.error
          : "supervisor_response_invalid",
      ), value.data?.executed===false ? {data:{executed:false}} : {});
    return value.result;
  }
  subscribe(callback: (event: SupervisorNotification) => void): () => void {
    this.listeners.add(callback);
    if (!this.watch) {
      const reconnect = () => {
        if (!this.listeners.size) return;
        this.watch?.abort();
        this.watch = new AbortController();
        void this.follow(this.watch);
      };
      this.unwatchConnection = this.source.onChange?.(reconnect) ?? null;
      reconnect();
    }
    return () => {
      this.listeners.delete(callback);
      if (!this.listeners.size) {
        this.watch?.abort();
        this.watch = null;
        this.unwatchConnection?.();
        this.unwatchConnection = null;
      }
    };
  }
  close(): void {
    this.listeners.clear();
    this.watch?.abort();
    this.watch = null;
    this.unwatchConnection?.();
    this.unwatchConnection = null;
  }
  private async follow(controller: AbortController): Promise<void> {
    let backoff = 250;
    while (!controller.signal.aborted) {
      try {
        const connection = await this.connection();
        const response = await fetch(new URL("v1/events", connection.url), {
          headers: {
            ...this.headers(connection.key),
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
              if (controller.signal.aborted) break;
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
