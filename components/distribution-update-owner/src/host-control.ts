import { createServer, type ServerResponse } from "node:http";
import { createHash, timingSafeEqual, randomUUID } from "node:crypto";
import {
  lstat,
  readFile,
  mkdir,
  writeFile,
  rename,
  unlink,
} from "node:fs/promises";
import { dirname, isAbsolute } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import {
  identity,
  same,
  token,
  type RunningIdentity,
  type AdmissionLease,
  type RestartAdmissionContext,
  type AdmissionReconciliation,
} from "./types.js";
import type { DistributionUpdateOwner } from "./owner.js";
import {preferencesRecoveryFence, type PreferencesRecoveryFence} from "./app-reset.js";
import { serviceIdentity, type ServiceHostPort } from "./service-types.js";

export interface HostQuiescencePort {
  admitServiceStop?: ServiceHostPort["admitServiceStop"];
  inspectServiceLifecycle?: ServiceHostPort["inspectServiceLifecycle"];
  serviceStopReceipt?: ServiceHostPort["serviceStopReceipt"];
  releaseServiceStop?: ServiceHostPort["releaseServiceStop"];
  admitQuiescence(request: {
    commandId: string;
    purpose: "distribution-update";
    retryRefused: true;
  }): unknown | Promise<unknown>;
  inspectQuiescence(): unknown | Promise<unknown>;
  quiescenceReceipt(commandId: string): unknown | Promise<unknown>;
  releaseQuiescence(request: {
    fenceId: string;
    commandId: string;
    outcome: "ready" | "unchanged" | "unknown";
    evidence?: unknown;
  }): unknown | Promise<unknown>;
}
export interface HostControlConnection {
  url: string;
  token: string;
  dataScope: string;
}
export interface HostReleaseRequest {
  fenceId: string;
  commandId: string;
  purpose: string;
  instanceId: string;
  dataScope: string;
  outcome: "ready" | "unchanged";
  evidence: unknown;
}
export interface HostReleaseProof {
  verified: true;
  fenceId: string;
  commandId: string;
  outcome: "ready" | "unchanged";
  instanceId: string;
  dataScope: string;
  receiptId: string;
}
type RestartProof = ReturnType<DistributionUpdateOwner["restartProof"]>;
type RecordValue = Record<string, unknown>;
const MAX_REQUEST = 16384,
  MAX_RESPONSE = 65536;
const hash = (value: string) => createHash("sha256").update(value).digest();
const record = (value: unknown): RecordValue => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("host_control_invalid");
  return value as RecordValue;
};
function keys(value: RecordValue, allowed: string[], required = allowed) {
  if (
    Object.keys(value).some((k) => !allowed.includes(k)) ||
    required.some((k) => !Object.hasOwn(value, k))
  )
    throw Error("host_control_invalid");
}
function secret(value: unknown): string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value))
    throw Error("host_control_token_invalid");
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
    throw Error("host_control_endpoint_invalid");
  return url;
}
function running(value: unknown): RunningIdentity | null {
  if (value === null) return null;
  const v = record(value);
  if (typeof v.ready !== "boolean") throw Error("host_control_invalid");
  return {
    identity: identity(v.identity as RunningIdentity["identity"]),
    instanceId: token(v.instanceId),
    dataScope: token(v.dataScope),
    ready: v.ready,
  };
}
function fence(value: unknown) {
  if (value === undefined || value === null) return null;
  const v = record(value);
  if (
    !["checking", "held", "unknown", "releasing"].includes(String(v.phase)) ||
    (v.purpose !== "distribution-update" && v.purpose !== "recovery")
  )
    throw Error("host_control_invalid");
  return {
    fenceId: token(v.fenceId),
    commandId: token(v.commandId),
    purpose: v.purpose as string,
    instanceId: token(v.instanceId),
    dataScope: token(v.dataScope),
    phase: v.phase as string,
  };
}
function project(value: unknown) {
  if (value === undefined || value === null) return null;
  const v = record(value),
    out: RecordValue = {};
  for (const k of [
    "admitted",
    "executed",
    "intakeClosed",
    "released",
    "enabled",
  ])
    if (typeof v[k] === "boolean") out[k] = v[k];
  for (const k of ["commandId", "fenceId", "instanceId", "receiptId"])
    if (v[k] !== undefined) out[k] = token(v[k]);
  if (["ready", "unchanged", "unknown"].includes(String(v.outcome)))
    out.outcome = v.outcome;
  if (v.fence !== undefined) out.fence = fence(v.fence);
  if (v.evidence !== undefined) {
    const e = record(v.evidence);
    if (
      e.activeWork !== 0 ||
      e.intakeClosed !== true ||
      !Number.isSafeInteger(e.observedAt)
    )
      throw Error("host_control_invalid");
    out.evidence = {
      activeWork: 0,
      intakeClosed: true,
      instanceId: token(e.instanceId),
      dataScope: token(e.dataScope),
      observedAt: e.observedAt,
    };
  }
  return out;
}
async function privateJSON(path: string) {
  if (!isAbsolute(path)) throw Error("private_file_required");
  const info = await lstat(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.size > 65536 ||
    (process.platform !== "win32" && (info.mode & 0o077) !== 0)
  )
    throw Error("private_file_required");
  return JSON.parse(await readFile(path, "utf8"));
}
async function privateToken(path: string) {
  if (!isAbsolute(path)) throw Error("private_file_required");
  const info = await lstat(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.size > 128 ||
    (process.platform !== "win32" && (info.mode & 0o077) !== 0)
  )
    throw Error("private_file_required");
  return secret((await readFile(path, "utf8")).trim());
}
export async function readHostControlConnection(
  file: string,
): Promise<HostControlConnection> {
  const v = record(await privateJSON(file));
  keys(v, ["schema", "url", "tokenFile", "dataScope"]);
  if (v.schema !== "distribution-host-control-v1")
    throw Error("host_control_invalid");
  return {
    url: endpoint(String(v.url)).href,
    token: await privateToken(String(v.tokenFile)),
    dataScope: token(v.dataScope),
  };
}
/** Re-read the private connection on every command and event reconnect, so a
 * release follows the authenticated replacement host rather than an old port. */
export function connectHostControlFile(
  file: string,
  dataScope: string,
): HostControlClient {
  return new HostControlClient({
    dataScope,
    connect: () => readHostControlConnection(file),
  });
}

/** Host-owned verifier: reads supervisor authority through its authenticated
 * client. The release request's evidence/assertions are deliberately ignored. */
export function createHostReleaseVerifier(options: {
  supervisor: {
    restartProof(commandId: string): Promise<RestartProof> | RestartProof;
  };
  inspectRunning(): Promise<RunningIdentity | null> | RunningIdentity | null;
}) {
  return async (request: HostReleaseRequest): Promise<HostReleaseProof> => {
    if (request.purpose !== "distribution-update")
      throw Error("supervisor_proof_unconfirmed");
    const proof = await options.supervisor.restartProof(
        token(request.commandId),
      ),
      actual = running(await options.inspectRunning());
    if (
      !proof ||
      proof.schema !== "distribution-restart-proof-v1" ||
      proof.commandId !== request.commandId ||
      proof.purpose !== request.purpose ||
      proof.dataScope !== request.dataScope ||
      !actual?.ready ||
      actual.dataScope !== request.dataScope ||
      proof.admission?.fenceId !== request.fenceId ||
      proof.admission.instanceId !== request.instanceId ||
      proof.admission.dataScope !== request.dataScope ||
      proof.admission.activeWork !== 0 ||
      proof.admission.intakeClosed !== true
    )
      throw Error("supervisor_proof_unconfirmed");
    if (request.outcome === "ready") {
      if (
        proof.status !== "succeeded" ||
        proof.phase !== "ready" ||
        !proof.target ||
        !same(proof.target, actual.identity) ||
        proof.instanceId !== actual.instanceId ||
        proof.previousInstanceId !== request.instanceId ||
        actual.instanceId === request.instanceId
      )
        throw Error("supervisor_proof_unconfirmed");
    } else if (request.outcome === "unchanged") {
      if (
        proof.status !== "failed" ||
        proof.phase !== "pre_restart_refused" ||
        proof.instanceId !== null ||
        !proof.admittedRunning ||
        !same(proof.admittedRunning.identity, actual.identity) ||
        proof.admittedRunning.instanceId !== actual.instanceId ||
        actual.instanceId !== request.instanceId ||
        proof.admittedRunning.dataScope !== actual.dataScope
      )
        throw Error("supervisor_proof_unconfirmed");
    } else throw Error("supervisor_proof_unconfirmed");
    return {
      verified: true,
      fenceId: request.fenceId,
      commandId: request.commandId,
      outcome: request.outcome,
      instanceId: actual.instanceId,
      dataScope: actual.dataScope,
      receiptId: proof.commandId,
    };
  };
}

export async function serveHostControl(options: {
  host: HostQuiescencePort;
  inspectRunning(): Promise<RunningIdentity | null> | RunningIdentity | null;
  token: string;
  port?: number;
  onMayBeIdle?: (notify: () => void) => () => void;
  discovery?: { file: string; tokenFile: string; dataScope: string };
  /** Exact full configured recovery census, supplied by trusted composition. */
  recoveryOwners?: readonly string[];
}) {
  const recoveryOwners=options.recoveryOwners ? [...options.recoveryOwners].map(id=>token(id)).sort() : null;
  if(recoveryOwners && (!recoveryOwners.length || recoveryOwners.length>128 || new Set(recoveryOwners).size!==recoveryOwners.length))throw Error("host_control_invalid");
  const key = secret(options.token),
    epoch = randomUUID();
  let sequence = 0,
    active = 0,
    closed = false;
  const followers = new Set<ServerResponse>();
  const response = (res: ServerResponse, status: number, value: unknown) => {
    const data = JSON.stringify(value);
    res.writeHead(status, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(
      Buffer.byteLength(data) <= MAX_RESPONSE
        ? data
        : '{"ok":false,"error":"host_control_response_limit"}',
    );
  };
  const send = (res: ServerResponse) => {
    if (
      !res.write(
        `id: ${epoch}:${sequence}\ndata: ${JSON.stringify({ cursor: `${epoch}:${sequence}`, mayBeIdle: true })}\n\n`,
      )
    )
      res.destroy();
  };
  const notifyMayBeIdle = () => {
    sequence++;
    for (const res of followers) send(res);
  };
  const inspect = async () => {
    const actual = running(await options.inspectRunning());
    if (!actual) throw Error("host_control_not_ready");
    return actual;
  };
  const dispatch = async (input: RecordValue) => {
    keys(input, ["operation", "args", "dataScope"]);
    const actual = await inspect();
    if (input.dataScope !== actual.dataScope)
      throw Error("host_control_scope_mismatch");
    const args = record(input.args);
    if(input.operation === "verify-recovery") {
      const expected=preferencesRecoveryFence(args);
      const status=record(await options.host.inspectQuiescence());
      const held=fence(status.fence), owners=record(status.fence).owners;
      const admitted=project(await options.host.quiescenceReceipt(expected.commandId));
      if(!recoveryOwners || !actual.ready || status.enabled!==true || status.intakeClosed!==true || status.activeAdmissions!==0 || status.continuations!==0 || !Number.isSafeInteger(status.activeMaintenance) || Number(status.activeMaintenance)<0 || Number(status.activeMaintenance)>1 ||
        !held || held.phase!=="held" || held.purpose!=="recovery" ||
        !["fenceId","commandId","instanceId","dataScope"].every(k=>held[k as keyof typeof held]===expected[k as keyof PreferencesRecoveryFence]) ||
        held.instanceId!==actual.instanceId || held.dataScope!==actual.dataScope ||
        !Array.isArray(owners) || owners.some(id=>typeof id!=="string") || JSON.stringify([...owners].sort())!==JSON.stringify(recoveryOwners) ||
        admitted?.admitted!==true || admitted.fenceId!==held.fenceId || admitted.commandId!==held.commandId)
        throw Error("host_control_invalid");
      return {verified:true,...expected};
    }
    if (String(input.operation).startsWith("service-")) {
      const host = options.host;
      if (
        !host.admitServiceStop ||
        !host.inspectServiceLifecycle ||
        !host.serviceStopReceipt ||
        !host.releaseServiceStop
      )
        throw Error("host_control_unavailable");
      if (input.operation === "service-inspect") {
        keys(args, []);
        return serviceProjection(await host.inspectServiceLifecycle());
      }
      if (input.operation === "service-receipt") {
        keys(args, ["commandId"]);
        return serviceProjection(
          await host.serviceStopReceipt(token(args.commandId)),
        );
      }
      if (input.operation === "service-admit") {
        keys(args, ["commandId", "expected"]);
        return serviceProjection(
          await host.admitServiceStop({
            commandId: token(args.commandId),
            expected: serviceIdentity(args.expected as any),
          }),
        );
      }
      if (input.operation === "service-release") {
        keys(
          args,
          ["commandId", "fenceId", "outcome", "resumeCommandId", "evidence"],
          ["commandId", "fenceId", "outcome"],
        );
        if (
          !["resumed", "stop-refused", "unknown"].includes(String(args.outcome))
        )
          throw Error("host_control_invalid");
        return serviceProjection(
          await host.releaseServiceStop({
            commandId: token(args.commandId),
            fenceId: token(args.fenceId),
            outcome: args.outcome as "resumed" | "stop-refused" | "unknown",
            ...(args.resumeCommandId
              ? { resumeCommandId: token(args.resumeCommandId) }
              : {}),
            evidence: args.evidence,
          }),
        );
      }
      throw Error("host_control_unknown_operation");
    }
    if (input.operation === "running") {
      keys(args, []);
      return actual;
    }
    if (input.operation === "inspect") {
      keys(args, []);
      return project(await options.host.inspectQuiescence());
    }
    if (input.operation === "receipt") {
      keys(args, ["commandId"]);
      return project(
        await options.host.quiescenceReceipt(token(args.commandId)),
      );
    }
    if (input.operation === "admit") {
      keys(args, ["commandId", "purpose"]);
      if (args.purpose !== "distribution-update")
        throw Error("host_control_invalid");
      const commandId = token(args.commandId);
      const result = project(
        await options.host.admitQuiescence({
          commandId,
          purpose: "distribution-update",
          retryRefused: true,
        }),
      );
      if (result?.admitted === true) {
        const held = fence(
          record(await options.host.inspectQuiescence()).fence,
        );
        if (
          !held ||
          held.phase !== "held" ||
          held.commandId !== commandId ||
          held.fenceId !== result.fenceId ||
          held.purpose !== "distribution-update" ||
          held.dataScope !== actual.dataScope ||
          held.instanceId !== actual.instanceId
        )
          throw Error("host_admission_unknown");
        return { ...result, fence: held };
      }
      return result;
    }
    if (input.operation === "release") {
      keys(args, ["commandId", "purpose", "fenceId", "outcome"]);
      const commandId = token(args.commandId),
        fenceId = token(args.fenceId);
      if (
        args.purpose !== "distribution-update" ||
        !["ready", "unchanged", "unknown"].includes(String(args.outcome))
      )
        throw Error("host_control_invalid");
      const held = fence(record(await options.host.inspectQuiescence()).fence);
      if (!held) {
        const old = project(await options.host.quiescenceReceipt(commandId));
        if (
          old?.released === true &&
          old.intakeClosed === false &&
          old.commandId === commandId &&
          old.fenceId === fenceId &&
          old.outcome === args.outcome &&
          old.instanceId === actual.instanceId
        )
          return old;
        throw Error("host_release_unconfirmed");
      }
      if (
        held.commandId !== commandId ||
        held.fenceId !== fenceId ||
        held.purpose !== "distribution-update" ||
        held.dataScope !== actual.dataScope
      )
        throw Error("host_release_unconfirmed");
      // The host's configured verifier obtains supervisor proof itself. No
      // ready claim, participant list or release identity from this caller.
      return project(
        await options.host.releaseQuiescence({
          commandId,
          fenceId,
          outcome: args.outcome as "ready" | "unchanged" | "unknown",
          evidence: { supervisorCommandId: commandId },
        }),
      );
    }
    throw Error("host_control_unknown_operation");
  };
  const server = createServer(async (req, res) => {
    if (closed) {
      response(res, 503, { ok: false, error: "host_control_closed" });
      return;
    }
    if (
      req.headers.origin ||
      req.headers["x-amplifier-host-control"] !== "1" ||
      !timingSafeEqual(
        hash(String(req.headers.authorization ?? "")),
        hash("Bearer " + key),
      )
    ) {
      response(res, 401, { ok: false, error: "host_control_unauthorized" });
      return;
    }
    if (req.method === "GET" && req.url === "/v1/host-events") {
      if (followers.size >= 32) {
        response(res, 429, { ok: false, error: "host_control_capacity" });
        return;
      }
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
      });
      followers.add(res);
      send(res);
      const timer = setInterval(() => {
        if (!res.write(": keepalive\n\n")) res.destroy();
      }, 15000);
      timer.unref();
      res.on("close", () => {
        clearInterval(timer);
        followers.delete(res);
      });
      return;
    }
    if (req.method !== "POST" || req.url !== "/v1/host-control") {
      response(res, 404, { ok: false, error: "host_control_not_found" });
      return;
    }
    if (active >= 32) {
      response(res, 429, { ok: false, error: "host_control_capacity" });
      return;
    }
    if (!String(req.headers["content-type"]).startsWith("application/json")) {
      response(res, 415, { ok: false, error: "host_control_invalid" });
      return;
    }
    active++;
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > MAX_REQUEST) throw Error("host_control_request_limit");
        chunks.push(chunk);
      }
      const result = await dispatch(
        record(JSON.parse(Buffer.concat(chunks).toString("utf8"))),
      );
      response(res, 200, { ok: true, result });
    } catch (error) {
      const code =
        error instanceof Error &&
        [
          "host_control_invalid",
          "host_control_scope_mismatch",
          "host_control_not_ready",
          "host_control_unknown_operation",
          "host_control_request_limit",
          "host_release_unconfirmed",
          "host_admission_unknown",
        ].includes(error.message)
          ? error.message
          : "host_control_operation_unconfirmed";
      response(res, 400, { ok: false, error: code });
    } finally {
      active--;
    }
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  server.maxConnections = 96;
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? 0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string")
    throw Error("host_control_listen_failed");
  const url = `http://127.0.0.1:${address.port}/`;
  let unsubscribe: (() => void) | undefined;
  try {
    if (options.discovery) {
      const d = options.discovery;
      if (
        !isAbsolute(d.file) ||
        !isAbsolute(d.tokenFile) ||
        token(d.dataScope) !== (await inspect()).dataScope ||
        (await privateToken(d.tokenFile)) !== key
      )
        throw Error("private_file_required");
      await mkdir(dirname(d.file), { recursive: true, mode: 0o700 });
      const tmp = d.file + "." + randomUUID();
      await writeFile(
        tmp,
        JSON.stringify({
          schema: "distribution-host-control-v1",
          url,
          tokenFile: d.tokenFile,
          dataScope: d.dataScope,
        }) + "\n",
        { flag: "wx", mode: 0o600 },
      );
      await rename(tmp, d.file);
    }
    unsubscribe = options.onMayBeIdle?.(notifyMayBeIdle);
  } catch (error) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    throw error;
  }
  return {
    url,
    notifyMayBeIdle,
    async close() {
      closed = true;
      unsubscribe?.();
      for (const res of followers) res.end();
      server.closeIdleConnections();
      await new Promise<void>((resolve, reject) =>
        server.close((e) => (e ? reject(e) : resolve())),
      );
      if (options.discovery) {
        try {
          const v = await privateJSON(options.discovery.file);
          if (v.url === url) await unlink(options.discovery.file);
        } catch {
          /* Do not remove a replacement host's discovery. */
        }
      }
    },
  };
}

export class HostControlClient {
  readonly dataScope: string;
  private listeners = new Set<() => void>();
  private watch: AbortController | null = null;
  constructor(
    private options: {
      dataScope: string;
      connect(): Promise<HostControlConnection> | HostControlConnection;
    },
  ) {
    this.dataScope = token(options.dataScope);
  }
  private async connection() {
    const c = await this.options.connect();
    if (c.dataScope !== this.dataScope)
      throw Error("host_control_scope_mismatch");
    return { url: endpoint(c.url), token: secret(c.token) };
  }
  private headers(key: string) {
    return { Authorization: "Bearer " + key, "X-Amplifier-Host-Control": "1" };
  }
  async rpc(
    operation: string,
    args: RecordValue = {},
    signal?: AbortSignal,
  ): Promise<unknown> {
    const mutation = [
        "admit",
        "release",
        "service-admit",
        "service-release",
      ].includes(operation),
      uncertain = () =>
        Error(
          mutation
            ? "host_control_outcome_unknown"
            : "host_control_unavailable",
        );
    let response: Response;
    try {
      const c = await this.connection();
      response = await fetch(new URL("v1/host-control", c.url), {
        method: "POST",
        headers: {
          ...this.headers(c.token),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ operation, args, dataScope: this.dataScope }),
        redirect: "error",
        signal: signal
          ? AbortSignal.any([signal, AbortSignal.timeout(10000)])
          : AbortSignal.timeout(10000),
      });
    } catch {
      throw uncertain();
    }
    if (!response.body) throw uncertain();
    const reader = response.body.getReader();
    let size = 0,
      value;
    const chunks: Uint8Array[] = [];
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > MAX_RESPONSE) throw uncertain();
        chunks.push(chunk.value);
      }
      value = record(JSON.parse(Buffer.concat(chunks, size).toString("utf8")));
      if (typeof value.ok !== "boolean") throw uncertain();
    } catch {
      throw uncertain();
    } finally {
      await reader.cancel().catch(() => {});
    }
    if (!response.ok || value.ok !== true)
      throw Error(
        mutation ? "host_control_outcome_unknown" : "host_control_unavailable",
      );
    return value.result;
  }
  inspect = async (): Promise<RunningIdentity | null> =>
    running(await this.rpc("running"));
  inspectQuiescence = async () => project(await this.rpc("inspect"));
  verifyRecoveryFence = async (input: PreferencesRecoveryFence): Promise<void> => {
    const expected=preferencesRecoveryFence(input);
    const result=record(await this.rpc("verify-recovery",{...expected}));
    if(result.verified!==true || !Object.entries(expected).every(([k,v])=>result[k]===v))throw Error("host_control_invalid");
  };
  quiescenceReceipt = async (commandId: string) =>
    project(await this.rpc("receipt", { commandId: token(commandId) }));
  readonly service: ServiceHostPort = {
    admitServiceStop: (request) =>
      this.rpc("service-admit", {
        commandId: token(request.commandId),
        expected: serviceIdentity(request.expected),
      }),
    inspectServiceLifecycle: () => this.rpc("service-inspect"),
    serviceStopReceipt: (commandId) =>
      this.rpc("service-receipt", { commandId: token(commandId) }),
    releaseServiceStop: (request) => this.rpc("service-release", request),
  };
  admitRestart = async (
    context?: RestartAdmissionContext,
  ): Promise<AdmissionLease | null> => {
    if (
      !context ||
      context.purpose !== "distribution-update" ||
      context.dataScope !== this.dataScope
    )
      throw Error("host_admission_context_required");
    const commandId = token(context.commandId),
      result = project(
        await this.rpc(
          "admit",
          { commandId, purpose: context.purpose },
          context.signal,
        ),
      );
    if (
      result?.admitted === false &&
      result.executed === false &&
      result.intakeClosed === false &&
      result.commandId === commandId &&
      !result.fence
    )
      return null;
    const held = fence(result?.fence);
    if (
      result?.admitted !== true ||
      !held ||
      held.phase !== "held" ||
      held.commandId !== commandId ||
      held.purpose !== context.purpose ||
      held.dataScope !== this.dataScope ||
      held.fenceId !== result.fenceId
    )
      throw Error("host_admission_unknown");
    const e = record(result.evidence);
    if (e.instanceId !== held.instanceId || e.dataScope !== this.dataScope)
      throw Error("host_admission_unknown");
    return {
      evidence: {
        activeWork: 0,
        intakeClosed: true,
        instanceId: held.instanceId,
        dataScope: this.dataScope,
        observedAt: Number(e.observedAt),
        fenceId: held.fenceId,
      },
      release: (outcome) => this.release(commandId, held.fenceId, outcome),
    };
  };
  private async release(
    commandId: string,
    fenceId: string,
    outcome: "ready" | "unchanged" | "unknown",
  ) {
    const result = project(
      await this.rpc("release", {
        commandId,
        purpose: "distribution-update",
        fenceId,
        outcome,
      }),
    );
    if (outcome === "unknown") {
      const held = fence(result?.fence);
      if (
        result?.intakeClosed !== true ||
        held?.fenceId !== fenceId ||
        held.commandId !== commandId
      )
        throw Error("host_release_unconfirmed");
    } else if (
      result?.released !== true ||
      result.intakeClosed !== false ||
      result.commandId !== commandId ||
      result.fenceId !== fenceId ||
      result.outcome !== outcome
    )
      throw Error("host_release_unconfirmed");
  }
  reconcileAdmission = async (
    request: AdmissionReconciliation,
  ): Promise<void> => {
    if (
      request.dataScope !== this.dataScope ||
      request.purpose !== "distribution-update" ||
      !["ready", "unchanged"].includes(request.outcome)
    )
      throw Error("host_control_scope_mismatch");
    const result = await this.quiescenceReceipt(request.commandId),
      held = fence(result?.fence),
      id = held?.fenceId ?? result?.fenceId;
    await this.release(token(request.commandId), token(id), request.outcome);
  };
  onIdle = (callback: () => void): (() => void) => {
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
  };
  close() {
    this.listeners.clear();
    this.watch?.abort();
    this.watch = null;
  }
  private async follow(controller: AbortController) {
    let backoff = 250;
    while (!controller.signal.aborted) {
      try {
        const c = await this.connection(),
          response = await fetch(new URL("v1/host-events", c.url), {
            headers: this.headers(c.token),
            redirect: "error",
            signal: controller.signal,
          });
        if (!response.ok || !response.body)
          throw Error("host_events_unavailable");
        const reader = response.body.getReader(),
          decoder = new TextDecoder();
        let buffer = "";
        backoff = 250;
        try {
          while (!controller.signal.aborted) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            if (buffer.length > 65536) throw Error("host_events_limit");
            let end;
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const frame = buffer.slice(0, end);
              buffer = buffer.slice(end + 2);
              const data = frame
                .split("\n")
                .find((s) => s.startsWith("data: "));
              if (!data) continue;
              const event = record(JSON.parse(data.slice(6)));
              if (event.mayBeIdle !== true) continue;
              for (const notify of this.listeners)
                try {
                  Promise.resolve(notify()).catch(() => {});
                } catch {}
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
        }
      } catch {
        /* Observation reconnection never retries a mutation. */
      }
      if (!controller.signal.aborted)
        await delay(backoff, undefined, { signal: controller.signal }).catch(
          () => {},
        );
      backoff = Math.min(5000, backoff * 2);
    }
  }
}

/** Service projection excludes host exception text, paths and owner internals. */
function serviceProjection(input: unknown): unknown {
  if (input === null || input === undefined) return null;
  const value = record(input),
    out: RecordValue = {};
  for (const key of [
    "enabled",
    "admitted",
    "executed",
    "intakeClosed",
    "released",
  ])
    if (typeof value[key] === "boolean") out[key] = value[key];
  for (const key of ["commandId", "fenceId", "receiptId", "resumeCommandId"])
    if (value[key] !== undefined) out[key] = token(value[key]);
  if (value.purpose === "service-stop") out.purpose = "service-stop";
  if (["resumed", "stop-refused", "unknown"].includes(String(value.outcome)))
    out.outcome = value.outcome;
  for (const key of ["expected", "observed", "identity"])
    if (value[key]) out[key] = serviceIdentity(value[key] as any);
  if (value.evidence) {
    const e = record(value.evidence);
    out.evidence = {
      activeWork: e.activeWork,
      intakeClosed: e.intakeClosed,
      instanceId: e.instanceId,
      dataScope: e.dataScope,
      observedAt: e.observedAt,
    };
  }
  if (value.fence) {
    const f = record(value.fence);
    out.fence = {
      fenceId: token(f.fenceId),
      commandId: token(f.commandId),
      purpose: f.purpose,
      phase: f.phase,
      instanceId: token(f.instanceId),
      dataScope: token(f.dataScope),
      // Retain the exact authenticated census for an offline archive after
      // child exit. A caller's inventory alone cannot prove owner quiescence.
      ...(Array.isArray(f.owners) && f.owners.length <= 128 &&
        new Set(f.owners).size === f.owners.length
        ? { owners: f.owners.map((id: unknown) => token(id)) }
        : {}),
      ...(f.serviceIdentity
        ? { serviceIdentity: serviceIdentity(f.serviceIdentity as any) }
        : {}),
    };
  }
  return out;
}
