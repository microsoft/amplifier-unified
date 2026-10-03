import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID, randomBytes } from "node:crypto";
import { realpath } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import type { LaunchSpec } from "./lifecycle.js";
import { token } from "./types.js";
export interface OwnedChildIdentity {
  instanceId: string;
  dataScope: string;
  releaseDigest: string;
}
export interface OwnedExitProof {
  ownerReceiptId: string;
  ownerId: string;
  instanceId: string;
  observedAt: number;
  code: number | null;
  signal: string | null;
}
interface ChildRecord {
  child: ChildProcess;
  identity: OwnedChildIdentity;
  key: string;
  owned: boolean;
  stopRequested?: boolean;
  exited: Promise<OwnedExitProof>;
  exit?: OwnedExitProof;
}
function identity(value: OwnedChildIdentity): OwnedChildIdentity {
  if (!value || !/^[a-f0-9]{64}$/.test(value.releaseDigest))
    throw Error("invalid_owned_identity");
  return {
    instanceId: token(value.instanceId),
    dataScope: token(value.dataScope),
    releaseDigest: value.releaseDigest,
  };
}
const same = (a: OwnedChildIdentity, b: OwnedChildIdentity) =>
  a.instanceId === b.instanceId &&
  a.dataScope === b.dataScope &&
  a.releaseDigest === b.releaseDigest;
/** POSIX Node child ownership is the inherited IPC handle plus authenticated
 * instance, never its numeric PID. Construction cannot adopt an existing child.
 * No group kill, service discovery, liveness inference or automatic recovery. */
export class PosixProcessOwner {
  readonly ownerId: string;
  private current: ChildRecord | null = null;
  private starting = false;
  private exits = new Map<
    string,
    { identity: OwnedChildIdentity; proof: OwnedExitProof }
  >();
  constructor(options: {
    ownerId: string;
    stopMs?: number;
    handshakeMs?: number;
  }) {
    if (!["darwin", "linux"].includes(process.platform))
      throw Error("service_platform_unsupported");
    this.ownerId = token(options.ownerId);
    this.stopMs = duration(options.stopMs ?? 10000);
    this.handshakeMs = duration(options.handshakeMs ?? 10000);
  }
  private readonly stopMs: number;
  private readonly handshakeMs: number;
  /** Diagnostic only; never accepted as mutation authority. */
  get ownedPid() {
    return this.current?.child.pid ?? null;
  }
  assertOwned(expected: OwnedChildIdentity) {
    const value = identity(expected),
      record = this.current;
    if (
      !record ||
      !same(record.identity, value) ||
      !record.owned ||
      record.exit ||
      !record.child.connected ||
      record.child.exitCode !== null ||
      record.child.signalCode !== null
    )
      throw Error("process_ownership_unproven");
  }
  inspect() {
    const record = this.current;
    if (!record)
      return {
        state: "unknown" as const,
        reason: "ownership_unproven" as const,
      };
    if (record.exit)
      return {
        state: "exited" as const,
        identity: { ...record.identity },
        proof: { ...record.exit },
      };
    try {
      this.assertOwned(record.identity);
    } catch {
      return {
        state: "unknown" as const,
        reason: "ownership_unproven" as const,
      };
    }
    return {
      state: "running" as const,
      identity: { ...record.identity },
      ownerId: this.ownerId,
    };
  }
  exitProof(expected: OwnedChildIdentity): OwnedExitProof | null {
    const row = this.exits.get(token(expected.instanceId));
    return row && same(row.identity, identity(expected))
      ? { ...row.proof }
      : null;
  }
  async start(
    spec: LaunchSpec,
    expected: OwnedChildIdentity,
    signal: AbortSignal,
  ): Promise<void> {
    if (this.starting) throw Error("lifecycle_busy");
    this.starting = true;
    try {
      const value = identity(expected);
      if (this.current && !this.current.exit)
        throw Error("owned_process_exists");
      if (this.exits.has(value.instanceId))
        throw Error("instance_already_used");
      if (
        typeof spec.command !== "string" ||
        (await realpath(spec.command)) !== (await realpath(process.execPath)) ||
        !Array.isArray(spec.args) ||
        !spec.args.length ||
        !isAbsolute(spec.args[0]) ||
        spec.args.some((arg) => typeof arg !== "string")
      )
        throw Error("owned_node_entrypoint_required");
      signal.throwIfAborted();
      const key = randomBytes(32).toString("hex");
      const child = spawn(
        process.execPath,
        [
          fileURLToPath(new URL("./posix-child.js", import.meta.url)),
          ...spec.args,
        ],
        {
          cwd: spec.cwd,
          env: {
            ...spec.env,
            AMPLIFIER_OWNED_CONTROL_TOKEN: key,
            AMPLIFIER_DISTRIBUTION_INSTANCE_ID: value.instanceId,
            AMPLIFIER_DISTRIBUTION_DATA_SCOPE: value.dataScope,
          },
          shell: false,
          stdio: ["ignore", "ignore", "ignore", "ipc"],
        },
      );
      let resolveExit!: (proof: OwnedExitProof) => void;
      const record: ChildRecord = {
        child,
        identity: value,
        key,
        owned: false,
        exited: new Promise((resolve) => {
          resolveExit = resolve;
        }),
      };
      this.current = record;
      child.once("exit", (code, signal) => {
        const proof: OwnedExitProof = {
          ownerReceiptId: randomUUID(),
          ownerId: this.ownerId,
          instanceId: value.instanceId,
          observedAt: Date.now(),
          code,
          signal,
        };
        record.exit = proof;
        this.exits.set(value.instanceId, { identity: value, proof });
        resolveExit(proof);
      });
      // A spawn error or lost handshake does not authorize another launch.
      child.on("error", () => {});
      await new Promise<void>((resolve, reject) => {
        const finish = (error?: Error) => {
          clearTimeout(timer);
          child.off("message", message);
          child.off("error", failed);
          child.off("exit", ended);
          signal.removeEventListener("abort", cancelled);
          error ? reject(error) : resolve();
        };
        const message = (raw: unknown) => {
          const m = raw as Record<string, unknown>;
          if (
            m?.schema === "distribution-owned-child-v1" &&
            m.operation === "owned" &&
            m.key === key &&
            m.instanceId === value.instanceId
          ) {
            record.owned = true;
            finish();
          }
        };
        const failed = () => finish(Error("owned_launch_unconfirmed"));
        const ended = () => finish(Error("owned_process_exited"));
        const cancelled = () => finish(Error("owned_launch_unconfirmed"));
        const timer = setTimeout(failed, this.handshakeMs);
        child.on("message", message);
        child.once("error", failed);
        child.once("exit", ended);
        signal.addEventListener("abort", cancelled, { once: true });
        if (signal.aborted) cancelled();
      });
    } finally {
      this.starting = false;
    }
  }
  async stop(expected: OwnedChildIdentity): Promise<OwnedExitProof> {
    this.assertOwned(expected);
    const record = this.current!;
    if (record.stopRequested) throw Error("owned_stop_unconfirmed");
    record.stopRequested = true;
    // IPC send is tied to a kernel connection. A dead/disconnected/reused PID
    // cannot become the target. A lost response is unknown, never a retry.
    await new Promise<void>((resolve, reject) =>
      record.child.send(
        {
          schema: "distribution-owned-child-v1",
          operation: "stop",
          key: record.key,
          instanceId: expected.instanceId,
        },
        (error) =>
          error ? reject(Error("owned_stop_unconfirmed")) : resolve(),
      ),
    );
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        record.exited,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(Error("owned_stop_unconfirmed")),
            this.stopMs,
          );
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
}
function duration(value: number) {
  if (!Number.isSafeInteger(value) || value < 100 || value > 600000)
    throw Error("invalid_lifecycle_duration");
  return value;
}
