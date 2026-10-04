import {admissionAbortRequest, type AdmissionAbortBinding, type AdmissionAbortRequest, type HostAdmissionAbortReceipt} from './admission-abort.js';
import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { randomUUID } from "node:crypto";
import {
  prepared,
  same,
  token,
  type AdmissionLease,
  type LifecyclePort,
  type PreparedRelease,
  type RestartRequest,
  type RunningIdentity,
  type RestartAdmissionContext,
  type AdmissionReconciliation,
} from "./types.js";

export interface LaunchSpec {
  command: string;
  args: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}
export interface InitialProvisioningEvidence {
  kind: "pristine-installation";
  installationId: string;
  commandId: string;
  instanceId: string;
  dataScope: string;
  targetDigest: string;
}
export interface InitialProvisioningPort {
  /** Explicit installer authority, durably consumed BEFORE any process effect.
   * Missing discovery is never authority. Interrupted claims are not retried. */
  claim(request: RestartRequest): Promise<InitialProvisioningEvidence>;
}
export interface OwnedProcessOptions {
  /** Resolve a qualified opaque candidate handle. Never expose arbitrary command
   * execution through a user-facing update action. */
  resolve(target: PreparedRelease): Promise<LaunchSpec>;
  /** Must authenticate the observed peer, not just read an untrusted open port. */
  inspect(): Promise<RunningIdentity | null>;
  admitRestart(
    context?: RestartAdmissionContext,
  ): Promise<AdmissionLease | null>;
  reconcileAdmission?(request: AdmissionReconciliation): Promise<void>;
  inspectAdmissionFence?(commandId: string): Promise<AdmissionAbortBinding | null>;
  inspectAdmissionAbort?(commandId: string): Promise<HostAdmissionAbortReceipt | null>;
  abortAdmission?(request: AdmissionAbortRequest): Promise<HostAdmissionAbortReceipt>;
  initialProvisioning?: InitialProvisioningPort;
  readinessMs?: number;
  stopMs?: number;
}

/** Portable supervisor for children created by this adapter. It must live
 * outside the replaceable distribution process. It cannot adopt or signal an
 * existing system service. Use a separate injected adapter for that lifecycle. */
export class OwnedProcessLifecycle implements LifecyclePort {
  private child: ChildProcess | null = null;
  private mutation = false;
  private readonly readinessMs: number;
  private readonly stopMs: number;
  constructor(private readonly options: OwnedProcessOptions) {
    this.readinessMs = this.duration(options.readinessMs ?? 30000);
    this.stopMs = this.duration(options.stopMs ?? 10000);
  }
  inspect(): Promise<RunningIdentity | null> {
    return this.options.inspect();
  }
  admitRestart(
    context?: RestartAdmissionContext,
  ): Promise<AdmissionLease | null> {
    return this.options.admitRestart(context);
  }
  async reconcileAdmission(request: AdmissionReconciliation): Promise<void> {
    await this.options.reconcileAdmission?.(request);
  }
  async inspectAdmissionFence(commandId: string): Promise<AdmissionAbortBinding | null> {
    return this.options.inspectAdmissionFence?.(commandId) ?? null;
  }
  async inspectAdmissionAbort(commandId: string): Promise<HostAdmissionAbortReceipt | null> {
    if (!this.child || this.child.killed || this.child.exitCode !== null || this.child.signalCode !== null) throw Error('process_not_owned');
    return this.options.inspectAdmissionAbort?.(commandId) ?? null;
  }
  async abortAdmission(input: AdmissionAbortRequest): Promise<HostAdmissionAbortReceipt> {
    if (this.mutation || !this.options.abortAdmission) throw Error('admission_abort_unavailable');
    this.mutation = true;
    try {
      const request = admissionAbortRequest(input), child = this.child;
      if (!child || child.exitCode !== null || child.signalCode !== null || child.killed) throw Error('process_not_owned');
      const check = async () => {
        const actual = await this.inspect();
        if (this.child !== child || !actual?.ready || actual.instanceId !== request.instanceId ||
          actual.dataScope !== request.dataScope || !same(actual.identity,request.observed.identity)) throw Error('process_not_owned');
      };
      await check();
      const result = await this.options.abortAdmission(request);
      await check();
      return result;
    } finally { this.mutation = false; }
  }
  get ownedPid(): number | null {
    return this.child?.pid ?? null;
  }

  /** Explicit first launch by the external supervisor, never triggered by inspect. */
  async startInitial(
    target: PreparedRelease,
    dataScope: string,
    signal: AbortSignal = new AbortController().signal,
  ): Promise<void> {
    if (this.child) throw Error("owned_process_exists");
    await this.performRestart(
      {
        target,
        instanceId: randomUUID(),
        dataScope,
        previousInstanceId: null,
        commandId: `initial:${randomUUID()}`,
        signal,
      },
      true,
    );
  }
  async restart(request: RestartRequest): Promise<void> {
    return this.performRestart(request, false);
  }
  private async performRestart(
    request: RestartRequest,
    initial: boolean,
  ): Promise<void> {
    if (this.mutation) throw Error("lifecycle_busy");
    this.mutation = true;
    try {
      const target = prepared(request.target);
      token(request.instanceId);
      token(request.dataScope);
      const spec = await this.options.resolve(target);
      if (
        typeof spec.command !== "string" ||
        !spec.command ||
        !Array.isArray(spec.args) ||
        spec.args.some((arg) => typeof arg !== "string")
      )
        throw Error("invalid_launch_spec");
      request.signal.throwIfAborted();
      let current: RunningIdentity | null;
      if (initial && this.options.initialProvisioning) {
        const proof = await this.options.initialProvisioning.claim(request);
        if (
          request.previousInstanceId !== null ||
          proof.kind !== "pristine-installation" ||
          !token(proof.installationId) ||
          proof.commandId !== request.commandId ||
          proof.instanceId !== request.instanceId ||
          proof.dataScope !== request.dataScope ||
          proof.targetDigest !== target.identity.digest
        )
          throw Error("initial_provisioning_unconfirmed");
        current = null;
      } else current = await this.inspect();
      if ((current?.instanceId ?? null) !== request.previousInstanceId)
        throw Error("restart_identity_conflict");
      // We never signal a PID learned from the readiness endpoint or receipt.
      if (current && !this.child) throw Error("process_not_owned");
      await this.stop();
      request.signal.throwIfAborted();
      const child = spawn(spec.command, spec.args, {
        cwd: spec.cwd,
        env: {
          ...spec.env,
          AMPLIFIER_DISTRIBUTION_INSTANCE_ID: request.instanceId,
          AMPLIFIER_DISTRIBUTION_DATA_SCOPE: request.dataScope,
        },
        shell: false,
        stdio: "ignore",
        windowsHide: true,
      });
      this.child = child;
      const exited = () => {
        if (this.child === child) this.child = null;
      };
      child.once("exit", exited);
      await new Promise<void>((resolve, reject) => {
        child.once("error", () => {
          exited();
          reject(Error("launch_failed"));
        });
        child.once("spawn", resolve);
      });
      const until = Date.now() + this.readinessMs;
      while (Date.now() < until) {
        request.signal.throwIfAborted();
        if (this.child !== child) throw Error("process_exited");
        let observed: RunningIdentity | null = null;
        try {
          observed = await this.inspect();
        } catch {
          /* Connection may be unavailable while the owned child starts. */
        }
        if (
          observed?.ready &&
          observed.instanceId === request.instanceId &&
          observed.dataScope === request.dataScope &&
          same(observed.identity, target.identity)
        )
          return;
        await delay(Math.min(50, Math.max(1, until - Date.now())), undefined, {
          signal: request.signal,
        });
      }
      // A readiness deadline is not an inference timeout. The new process is
      // left running, and the owner records unknown for passive reconciliation.
      throw Error("readiness_unconfirmed");
    } finally {
      this.mutation = false;
    }
  }
  /** Explicit owned fixture/process cleanup; never part of a failed update's
   * automatic rollback. No process outside this adapter can be killed here. */
  async close(): Promise<void> {
    if (this.mutation) throw Error("lifecycle_busy");
    await this.stop();
  }
  private async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    if (child.exitCode !== null || child.signalCode !== null) {
      this.child = null;
      return;
    }
    const exited = new Promise<void>((resolve) =>
      child.once("exit", () => resolve()),
    );
    child.kill("SIGTERM");
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        exited,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(Error("owned_process_stop_unconfirmed")),
            this.stopMs,
          );
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  private duration(value: number): number {
    if (!Number.isSafeInteger(value) || value < 100 || value > 600000)
      throw Error("invalid_lifecycle_duration");
    return value;
  }
}
