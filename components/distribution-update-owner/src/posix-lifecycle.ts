import {admissionAbortRequest, type AdmissionAbortRequest} from './admission-abort.js';
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { PosixProcessOwner, type OwnedChildIdentity } from "./posix-process.js";
import type { OwnedProcessOptions } from "./lifecycle.js";
import { OwnedStartupError, startupFailure } from "./startup-diagnostics.js";
import {
  prepared,
  token,
  same,
  type LifecyclePort,
  type RestartRequest,
  type PreparedRelease,
  type RunningIdentity,
  type AdmissionReconciliation,
  type RestartAdmissionContext,
} from "./types.js";
const childIdentity = (running: RunningIdentity): OwnedChildIdentity => ({
  instanceId: running.instanceId,
  dataScope: running.dataScope,
  releaseDigest: running.identity.digest,
});
/** Production POSIX lifecycle; both updates and service operations share the
 * same owned child connection and single in-process mutation boundary. */
export class PosixOwnedProcessLifecycle implements LifecyclePort {
  readonly processes: PosixProcessOwner;
  private mutation = false;
  constructor(
    private readonly options: OwnedProcessOptions & { ownerId: string },
  ) {
    this.processes = new PosixProcessOwner(options);
  }
  get ownedPid() {
    return this.processes.ownedPid;
  }
  inspect() {
    return this.options.inspect();
  }
  admitRestart(context?: RestartAdmissionContext) {
    return this.options.admitRestart(context);
  }
  reconcileAdmission(request: AdmissionReconciliation) {
    if (!this.options.reconcileAdmission)
      return Promise.reject(Error("admission_reconciliation_unavailable"));
    return this.options.reconcileAdmission(request);
  }
  async inspectAdmissionFence(commandId: string) {
    if (!this.options.inspectAdmissionFence) return null;
    await this.inspectOwned();
    return this.options.inspectAdmissionFence(commandId);
  }
  async inspectAdmissionAbort(commandId: string) {
    if (!this.options.inspectAdmissionAbort) return null;
    await this.inspectOwned();
    return this.options.inspectAdmissionAbort(commandId);
  }
  async abortAdmission(input: AdmissionAbortRequest) {
    return this.mutate(async () => {
      if (!this.options.abortAdmission) throw Error('admission_abort_unavailable');
      const request = admissionAbortRequest(input);
      const check = async () => {
        const actual = await this.inspectOwned();
        if (actual.instanceId !== request.instanceId || actual.dataScope !== request.dataScope ||
          !same(actual.identity,request.observed.identity)) throw Error('process_ownership_unproven');
      };
      await check();
      const result = await this.options.abortAdmission(request);
      await check();
      return result;
    });
  }
  async inspectOwned(): Promise<RunningIdentity> {
    const actual = await this.inspect();
    if (!actual?.ready) throw Error("process_ownership_unproven");
    this.processes.assertOwned(childIdentity(actual));
    return actual;
  }
  async startInitial(
    target: PreparedRelease,
    dataScope: string,
    signal = new AbortController().signal,
  ) {
    const request: RestartRequest = {
      target: prepared(target),
      dataScope: token(dataScope),
      instanceId: randomUUID(),
      previousInstanceId: null,
      commandId: "initial:" + randomUUID(),
      signal,
    };
    await this.mutate(async () => {
      if (!this.options.initialProvisioning)
        throw Error("initial_authority_required");
      const spec = await this.options.resolve(request.target);
      request.signal.throwIfAborted();
      const proof = await this.options.initialProvisioning.claim(request);
      if (
        proof.kind !== "pristine-installation" ||
        !token(proof.installationId) ||
        proof.commandId !== request.commandId ||
        proof.instanceId !== request.instanceId ||
        proof.dataScope !== dataScope ||
        proof.targetDigest !== target.identity.digest
      )
        throw Error("initial_provisioning_unconfirmed");
      await this.processes.start(
        spec,
        {
          instanceId: request.instanceId,
          dataScope,
          releaseDigest: target.identity.digest,
        },
        signal,
      );
      await this.ready(request);
    });
  }
  async restart(request: RestartRequest) {
    await this.mutate(async () => {
      const target = prepared(request.target),
        actual = await this.inspectOwned();
      if (
        actual.instanceId !== request.previousInstanceId ||
        actual.dataScope !== request.dataScope ||
        request.instanceId === actual.instanceId
      )
        throw Error("restart_identity_conflict");
      const spec = await this.options.resolve(target);
      request.signal.throwIfAborted();
      await this.processes.stop(childIdentity(actual));
      request.signal.throwIfAborted();
      await this.processes.start(
        spec,
        {
          instanceId: token(request.instanceId),
          dataScope: token(request.dataScope),
          releaseDigest: target.identity.digest,
        },
        request.signal,
      );
      await this.ready(request);
    });
  }
  /** Trusted service owner must first persist and obtain its service-stop fence. */
  async stopOwned(expected: OwnedChildIdentity) {
    return this.mutate(async () => {
      const actual = await this.inspectOwned();
      if (
        actual.instanceId !== expected.instanceId ||
        actual.dataScope !== expected.dataScope ||
        actual.identity.digest !== expected.releaseDigest
      )
        throw Error("service_identity_conflict");
      return this.processes.stop(expected);
    });
  }
  /** Trusted service owner must verify its durable stopped receipt before this
   * explicit call. Not reachable through update retry or readiness inspection. */
  async resumeOwned(request: RestartRequest) {
    return this.mutate(async () => {
      // A durable stopped receipt authorizes explicit resumption, but never
      // replacement of another service now occupying this installation.
      let existing: RunningIdentity | null = null;
      try {
        existing = await this.inspect();
      } catch {
        /* Closed host control. */
      }
      if (existing) throw Error("existing_service_not_owned");
      const target = prepared(request.target),
        spec = await this.options.resolve(target);
      await this.processes.start(
        spec,
        {
          instanceId: token(request.instanceId),
          dataScope: token(request.dataScope),
          releaseDigest: target.identity.digest,
        },
        request.signal,
      );
      await this.ready(request);
    });
  }
  /** Test-owned cleanup only; real stop requires the service owner's held fence. */
  async close() {
    const actual = this.processes.inspect();
    if (actual.state === "running")
      await this.mutate(() => this.processes.stop(actual.identity));
  }
  private async ready(request: RestartRequest) {
    const until = Date.now() + (this.options.readinessMs ?? 30000);
    while (Date.now() < until) {
      request.signal.throwIfAborted();
      this.processes.assertStarting({
        instanceId: request.instanceId,
        dataScope: request.dataScope,
        releaseDigest: request.target.identity.digest,
      });
      let actual: RunningIdentity | null = null;
      try {
        actual = await this.inspect();
      } catch {
        /* Authenticated endpoint may not yet be published. */
      }
      if (
        actual?.ready &&
        actual.instanceId === request.instanceId &&
        actual.dataScope === request.dataScope &&
        same(actual.identity, request.target.identity)
      ) {
        this.processes.confirmReady(childIdentity(actual));
        return;
      }
      await delay(25, undefined, { signal: request.signal });
    }
    throw new OwnedStartupError(startupFailure("readiness_unconfirmed", "readiness"));
  }
  private async mutate<T>(operation: () => Promise<T>): Promise<T> {
    if (this.mutation) throw Error("lifecycle_busy");
    this.mutation = true;
    try {
      return await operation();
    } finally {
      this.mutation = false;
    }
  }
}
