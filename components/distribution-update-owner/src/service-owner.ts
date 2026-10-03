import { startupFailureFrom } from "./startup-diagnostics.js";
import { isDeepStrictEqual } from "node:util";
import { constants as osConstants } from "node:os";
import type { ExistingStateHandoffCommand, ExistingStateHandoffClaim,
  ExistingStateHandoffProof, ExistingStateHandoffSource } from "./existing-state-types.js";
import { randomUUID } from "node:crypto";
import { ServiceStore } from "./service-store.js";
import {
  serviceIdentity,
  sameService,
  serviceReceipt,
  type ServiceIdentity,
  type ServiceCommand,
  type ServiceRecord,
  type ServiceReceipt,
  type ServiceHostPort,
  type ServiceReleaseProof,
  type ServiceReleaseRequest,
} from "./service-types.js";
import type { PosixOwnedProcessLifecycle } from "./posix-lifecycle.js";
import type { VerifiedRuntimeIdentity } from "./runtime-identity.js";
import {
  prepared,
  token,
  type PreparedRelease,
  type ReleasePort,
  identity,
  same,
} from "./types.js";

function handoffCommand(value: ExistingStateHandoffCommand): ExistingStateHandoffCommand {
  const expected = serviceIdentity(value.expected), target = identity(value.target);
  if (target.digest !== expected.releaseDigest || !/^[a-f0-9]{64}$/.test(value.dataBindingDigest) ||
      !Array.isArray(value.participantIds) || !value.participantIds.length || value.participantIds.length > 128 ||
      new Set(value.participantIds).size !== value.participantIds.length || value.commandId === value.stoppedCommandId)
    throw Error("existing_state_handoff_binding_invalid");
  return {commandId:token(value.commandId), stoppedCommandId:token(value.stoppedCommandId), expected, target,
    dataBindingDigest:value.dataBindingDigest, participantIds:value.participantIds.map(id=>token(id)).sort()};
}
function handoffClaim(value: ExistingStateHandoffClaim): ExistingStateHandoffClaim {
  const command = handoffCommand(value), next = serviceIdentity(value.next);
  if (next.instanceId === command.expected.instanceId ||
      !sameService({...next,instanceId:command.expected.instanceId},command.expected))
    throw Error("existing_state_handoff_binding_invalid");
  return {...command,next};
}
function qualifiedStop(stopped: ServiceRecord | null, claim: ExistingStateHandoffClaim) {
  if (!stopped || stopped.operation !== "stop" || stopped.commandId !== claim.stoppedCommandId ||
      stopped.status !== "stopped" || stopped.phase !== "stopped" || !stopped.target ||
      !same(stopped.target.identity, claim.target) || !sameService(stopped.expected,claim.expected) ||
      !stopped.fenceId || !stopped.exitProof ||
      stopped.exitProof.ownerId !== claim.expected.ownerId || stopped.exitProof.instanceId !== claim.expected.instanceId ||
      !Number.isSafeInteger(stopped.exitProof.observedAt) || stopped.exitProof.observedAt < 0 || !stopped.exitProof.ownerReceiptId ||
      !(stopped.exitProof.code === null || Number.isInteger(stopped.exitProof.code) && stopped.exitProof.code >= 0 && stopped.exitProof.code <= 255) ||
      !(stopped.exitProof.signal === null || Object.hasOwn(osConstants.signals,stopped.exitProof.signal)) ||
      !Array.isArray(stopped.qualifiedOwners) ||
      !isDeepStrictEqual([...stopped.qualifiedOwners].sort(),claim.participantIds))
    throw Error("existing_state_qualified_stop_required");
  return stopped;
}
const object = (v: unknown): Record<string, any> => {
  if (!v || typeof v !== "object" || Array.isArray(v))
    throw Error("service_evidence_unconfirmed");
  return v as Record<string, any>;
};
const terminal = (r: ServiceReceipt) =>
  r.status !== "running" && r.admissionSettlement?.state !== "pending";
/** Compose service identity from verified installed runtime identity plus the
 * installation/owner binding supplied by its trusted supervisor launch. */
export async function inspectRuntimeService(
  runtime: VerifiedRuntimeIdentity,
): Promise<ServiceIdentity> {
  const actual = await runtime.inspectRunning();
  if (!actual.ready) throw Error("service_runtime_not_ready");
  return serviceIdentity({
    installationId: process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID!,
    ownerId: process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID!,
    dataScope: actual.dataScope,
    instanceId: actual.instanceId,
    releaseDigest: actual.identity.digest,
  });
}
export interface ServiceOwnerOptions {
  directory: string;
  installationId: string;
  dataScope: string;
  ownerId: string;
  host: ServiceHostPort;
  lifecycle: PosixOwnedProcessLifecycle;
  releases: Pick<ReleasePort, "verify">;
  currentRelease(): PreparedRelease | null;
  updateMutationBlocked?: () => boolean;
  onChange?: (receipt: ServiceReceipt) => void | Promise<void>;
}
/** Durable explicit stop/resume for this installation. OS ownership is provided
 * solely by the supervisor's retained child channel. Construction/inspection
 * never starts or adopts a process, and unknown effects cannot be replayed. */
export class ServiceLifecycleOwner {
  private store: ServiceStore;
  private active: Promise<void> | null = null;
  private closing = false;
  private waiters = new Map<string, Set<(r: ServiceReceipt) => void>>();
  private binding: Pick<
    ServiceIdentity,
    "installationId" | "dataScope" | "ownerId"
  >;
  constructor(private options: ServiceOwnerOptions) {
    this.binding = {
      installationId: token(options.installationId),
      dataScope: token(options.dataScope),
      ownerId: token(options.ownerId),
    };
    if (options.lifecycle.processes.ownerId !== options.ownerId)
      throw Error("service_owner_binding_conflict");
    this.store = new ServiceStore(options.directory, this.binding);
  }
  stop(command: ServiceCommand) {
    return this.submit("stop", command);
  }
  resume(command: ServiceCommand & { stoppedCommandId: string }) {
    return this.submit("resume", command, token(command.stoppedCommandId));
  }
  adopt(command: ServiceCommand) {
    return this.submit("adopt", command);
  }
  /** Trusted source launcher only. Retires its genuine stopped authority once;
   * a destination cannot supply a claimed exit or infer one from a saved PID. */
  claimExistingStateHandoff(input: ExistingStateHandoffClaim): ExistingStateHandoffProof {
    if (this.closing) throw Error("service_handoff_source_busy");
    const claim = handoffClaim(input), stopped = qualifiedStop(this.store.read(claim.stoppedCommandId),claim);
    if (stopped.handoffRetired) {
      if (!isDeepStrictEqual(stopped.handoffClaim,claim)) throw Error("service_handoff_already_claimed");
      return {schema:"distribution-existing-state-handoff-v1",claim,stopped:structuredClone(stopped)};
    }
    const actual = this.options.lifecycle.processes.exitProof(claim.expected),
      current = this.options.lifecycle.processes.inspect();
    if (stopped.resumeCommandId || !actual || !isDeepStrictEqual(actual,stopped.exitProof) ||
        current.state !== "exited" || current.identity.instanceId !== claim.expected.instanceId ||
        current.identity.dataScope !== claim.expected.dataScope || current.identity.releaseDigest !== claim.expected.releaseDigest ||
        this.store.all().some(op=>op.status === "running" || op.status === "unknown" ||
          (op.admissionSettlement && op.admissionSettlement.state !== "settled")))
      throw Error("existing_state_exit_authority_required");
    stopped.handoffClaim = claim;
    stopped.handoffRetired = true;
    stopped.resumeCommandId = claim.commandId; // retire BEFORE another launcher can act
    this.persist(stopped);
    return {schema:"distribution-existing-state-handoff-v1",claim,stopped:structuredClone(stopped)};
  }
  /** Explicit existing-state handoff. Source retirement is an uncertain external
   * effect; persist acceptance first and never reconstruct this work on reopen. */
  handoff(input: ExistingStateHandoffCommand, source: ExistingStateHandoffSource): ServiceReceipt {
    if (this.closing) throw Error("service_owner_closed");
    const command = handoffCommand(input);
    if (Object.entries(this.binding).some(([key,value])=>command.expected[key as keyof ServiceIdentity] !== value))
      throw Error("service_owner_binding_conflict");
    const accepted = this.store.accept({commandId:command.commandId, operation:"handoff",expected:command.expected,
      stoppedCommandId:command.stoppedCommandId,status:"running",phase:"accepted",updatedAt:Date.now()},["handoff",command]);
    if (!accepted.fresh) return serviceReceipt(accepted.record);
    const op = accepted.record;
    if (this.active || this.options.updateMutationBlocked?.() || this.options.lifecycle.ownedPid !== null || this.store.all().some(other=>other.commandId !== op.commandId)) {
      this.finish(op,"refused","stop_refused","existing_state_fresh_owner_required");
      return serviceReceipt(op);
    }
    this.emit(op);
    const work = Promise.resolve().then(()=>this.performHandoff(op,command,source)).catch(error=>{
      const failure = startupFailureFrom(error);
      if (failure) op.startupFailure = failure;
      this.finish(op,"unknown",op.phase,"service_handoff_unconfirmed");
    }).finally(()=>{if(this.active === work)this.active = null;});
    this.active = work;
    return serviceReceipt(op);
  }
  private async performHandoff(op: ServiceRecord, command: ExistingStateHandoffCommand, source: ExistingStateHandoffSource) {
    const current = this.options.currentRelease();
    if (!current || !same(current.identity,command.target) ||
        !(await this.options.releases.verify(current,{commandId:op.commandId,signal:new AbortController().signal}))) {
      this.finish(op,"refused","stop_refused","retained_release_unverified");return;
    }
    op.observed = {...command.expected,instanceId:randomUUID()};
    op.handoffClaim = {...command,next:op.observed};
    op.phase = "handoff_requested";
    this.persist(op);
    const proof = await source.claim(op.handoffClaim);
    if (proof?.schema !== "distribution-existing-state-handoff-v1" ||
        !isDeepStrictEqual(proof.claim,op.handoffClaim)) throw Error("existing_state_source_unconfirmed");
    const stopped = qualifiedStop(proof.stopped,op.handoffClaim);
    if (stopped.handoffRetired !== true || stopped.resumeCommandId !== op.commandId || !isDeepStrictEqual(stopped.handoffClaim,op.handoffClaim))
      throw Error("existing_state_source_not_retired");
    // Transfer the genuine stop/exit/fence record into a fresh destination ledger.
    // The original has already consumed its resume right. This destination's
    // durable unknown/running operation blocks every competing resume on crash.
    const incoming: ServiceRecord = {commandId:command.stoppedCommandId,operation:"stop",status:"stopped",phase:"stopped",
      expected:command.expected,updatedAt:Date.now(),fenceId:token(stopped.fenceId),qualifiedOwners:[...command.participantIds],
      exitProof:{ownerReceiptId:token(stopped.exitProof!.ownerReceiptId),ownerId:command.expected.ownerId,
        instanceId:command.expected.instanceId,observedAt:stopped.exitProof!.observedAt,
        code:stopped.exitProof!.code,signal:stopped.exitProof!.signal},
      handoffClaim:op.handoffClaim,target:prepared(current)};
    this.store.accept(incoming,["handoff-stop",op.handoffClaim]);
    await this.performResume(op);
  }
  receipt(commandId: string) {
    const r = this.store.read(token(commandId));
    return r ? serviceReceipt(r) : null;
  }
  /** Authenticated supervisor proof lookup, not a browser-supplied assertion. */
  proof(commandId: string) {
    return this.receipt(commandId);
  }
  waitFor(commandId: string): Promise<ServiceReceipt> {
    const r = this.receipt(commandId);
    if (!r) throw Error("unknown_command");
    if (terminal(r)) return Promise.resolve(r);
    return new Promise((resolve) => {
      const set = this.waiters.get(commandId) ?? new Set();
      set.add(resolve);
      this.waiters.set(commandId, set);
    });
  }
  async inspect() {
    const retired = this.store.all().find(r=>r.handoffRetired);
    if (retired) return {state:"retired" as const, identity:retired.expected, handoffCommandId:retired.resumeCommandId!};
    const latest = this.store
      .all()
      .find(
        (r) =>
          r.operation === "stop" &&
          r.status === "stopped" &&
          !r.resumeCommandId,
      );
    if (latest)
      return {
        state: "stopped" as const,
        identity: latest.expected,
        stoppedReceiptId: latest.commandId,
        intakeClosed: true,
      };
    try {
      const observed = await this.observed();
      return {
        state: "running" as const,
        identity: observed,
        ownership: "verified" as const,
      };
    } catch {
      return {
        state: "unknown" as const,
        reason: "ownership_unproven" as const,
      };
    }
  }
  blocksUpdates() {
    return this.store
      .all()
      .some(
        (r) =>
          r.handoffRetired || r.status === "running" ||
          r.status === "unknown" ||
          (r.admissionSettlement &&
            r.admissionSettlement.state !== "settled") ||
          (r.operation === "stop" &&
            r.status === "stopped" &&
            !r.resumeCommandId),
      );
  }
  private submit(
    operation: "stop" | "resume" | "adopt",
    command: ServiceCommand,
    stoppedCommandId?: string,
  ) {
    if (this.closing) throw Error("service_owner_closed");
    const expected = serviceIdentity(command.expected),
      commandId = token(command.commandId);
    if (
      Object.entries(this.binding).some(
        ([k, v]) => expected[k as keyof ServiceIdentity] !== v,
      )
    )
      throw Error("service_owner_binding_conflict");
    const accepted = this.store.accept(
      {
        commandId,
        operation,
        expected,
        status: "running",
        phase: "accepted",
        updatedAt: Date.now(),
        ...(stoppedCommandId ? { stoppedCommandId } : {}),
      },
      [operation, expected, stoppedCommandId ?? null],
    );
    if (!accepted.fresh) return serviceReceipt(accepted.record);
    const op = accepted.record;
    if (this.store.all().some(r=>r.handoffRetired)) {
      this.finish(op,"refused","stop_refused","service_authority_retired");
      return serviceReceipt(op);
    }
    if (operation === "adopt") {
      this.finish(
        op,
        "refused",
        "ownership_unproven",
        "process_ownership_unproven",
      );
      return serviceReceipt(op);
    }
    if (
      this.options.updateMutationBlocked?.() ||
      this.store
        .all()
        .some(
          (r) =>
            r.commandId !== commandId &&
            (r.status === "running" ||
              r.status === "unknown" ||
              (r.admissionSettlement &&
                r.admissionSettlement.state !== "settled")),
        )
    ) {
      this.finish(op, "refused", "stop_refused", "service_unresolved");
      return serviceReceipt(op);
    }
    this.emit(op);
    const work = (this.active ?? Promise.resolve())
      .then(() =>
        operation === "stop" ? this.performStop(op) : this.performResume(op),
      )
      .catch(() => {
        this.finish(op, "unknown", op.phase, "service_effect_unconfirmed");
      })
      .finally(() => {
        if (this.active === work) this.active = null;
      });
    this.active = work;
    return serviceReceipt(op);
  }
  private persist(op: ServiceRecord) {
    op.updatedAt = Date.now();
    this.store.write(op);
    this.emit(op);
  }
  private emit(op: ServiceRecord) {
    const r = serviceReceipt(op);
    try {
      Promise.resolve(this.options.onChange?.(r)).catch(() => {});
    } catch {}
    if (terminal(r)) {
      for (const resolve of this.waiters.get(op.commandId) ?? []) resolve(r);
      this.waiters.delete(op.commandId);
    }
  }
  private finish(
    op: ServiceRecord,
    status: ServiceRecord["status"],
    phase: ServiceRecord["phase"],
    errorCode?: string,
  ) {
    op.status = status;
    op.phase = phase;
    if (errorCode) op.errorCode = errorCode;
    else delete op.errorCode;
    this.persist(op);
  }
  private async observed(): Promise<ServiceIdentity> {
    const actual = await this.options.lifecycle.inspectOwned();
    if (actual.dataScope !== this.binding.dataScope)
      throw Error("service_identity_conflict");
    return {
      ...this.binding,
      instanceId: actual.instanceId,
      releaseDigest: actual.identity.digest,
    };
  }
  private async exact(expected: ServiceIdentity) {
    if (!sameService(expected, await this.observed()))
      throw Error("service_identity_conflict");
  }
  private async performStop(op: ServiceRecord) {
    try {
      await this.exact(op.expected);
      const target = this.options.currentRelease();
      if (!target || target.identity.digest !== op.expected.releaseDigest)
        throw Error("service_release_unverified");
      op.target = prepared(target);
      if (
        !(await this.options.releases.verify(target, {
          commandId: op.commandId,
          signal: new AbortController().signal,
        }))
      )
        throw Error("service_release_unverified");
    } catch {
      this.finish(
        op,
        "refused",
        "ownership_unproven",
        "process_ownership_unproven",
      );
      return;
    }
    op.phase = "admission_requested";
    op.admissionSettlement = { state: "unknown", updatedAt: Date.now() };
    this.persist(op);
    let admitted: Record<string, any>;
    try {
      admitted = object(
        await this.options.host.admitServiceStop({
          commandId: op.commandId,
          expected: op.expected,
        }),
      );
    } catch {
      this.finish(
        op,
        "unknown",
        "admission_requested",
        "service_admission_unconfirmed",
      );
      return;
    }
    if (
      admitted.admitted === false &&
      admitted.executed === false &&
      admitted.intakeClosed === false &&
      admitted.purpose === "service-stop" &&
      sameService(admitted.expected, op.expected)
    ) {
      delete op.admissionSettlement;
      op.noEffect = true;
      this.finish(op, "refused", "stop_refused", "service_busy");
      return;
    }
    try {
      const held = object(
          object(await this.options.host.inspectServiceLifecycle()).fence,
        ),
        e = object(admitted.evidence);
      if (
        admitted.admitted !== true ||
        admitted.commandId !== op.commandId ||
        admitted.purpose !== "service-stop" ||
        !sameService(admitted.expected, op.expected) ||
        held.phase !== "held" ||
        held.commandId !== op.commandId ||
        held.fenceId !== admitted.fenceId ||
        held.purpose !== "service-stop" ||
        !sameService(held.serviceIdentity, op.expected) ||
        held.instanceId !== op.expected.instanceId ||
        held.dataScope !== op.expected.dataScope ||
        e.activeWork !== 0 ||
        e.intakeClosed !== true ||
        e.instanceId !== op.expected.instanceId ||
        e.dataScope !== op.expected.dataScope
      )
        throw Error("service_admission_unconfirmed");
      op.fenceId = token(admitted.fenceId);
      if (Array.isArray(held.owners) && held.owners.length <= 128 &&
          new Set(held.owners).size === held.owners.length) {
        op.qualifiedOwners = held.owners.map((id: unknown) => token(id));
      }
      delete op.admissionSettlement;
      op.phase = "held";
      this.persist(op);
    } catch {
      this.finish(
        op,
        "unknown",
        "admission_requested",
        "service_admission_unconfirmed",
      );
      return;
    }
    try {
      await this.exact(op.expected);
    } catch {
      // An unavailable observation cannot establish an unchanged live instance.
      this.finish(op, "unknown", "held", "service_identity_unconfirmed");
      return;
    }
    op.phase = "stop_requested";
    this.persist(op);
    try {
      op.exitProof = await this.options.lifecycle.stopOwned(op.expected);
      this.finish(op, "stopped", "stopped");
    } catch {
      this.finish(op, "unknown", "stop_requested", "service_stop_unconfirmed");
    }
  }
  private async performResume(op: ServiceRecord) {
    const stopped = this.store.read(op.stoppedCommandId!);
    if (
      !stopped ||
      stopped.operation !== "stop" ||
      stopped.status !== "stopped" ||
      stopped.phase !== "stopped" ||
      !stopped.target ||
      !stopped.exitProof ||
      !stopped.fenceId ||
      !sameService(stopped.expected, op.expected) ||
      stopped.exitProof.instanceId !== op.expected.instanceId ||
      stopped.exitProof.ownerId !== this.binding.ownerId ||
      stopped.resumeCommandId
    ) {
      this.finish(op, "refused", "stop_refused", "stopped_receipt_required");
      return;
    }
    try {
      if (
        !(await this.options.releases.verify(stopped.target, {
          commandId: op.commandId,
          signal: new AbortController().signal,
        }))
      )
        throw Error("retained_release_unverified");
    } catch {
      this.finish(op, "refused", "stop_refused", "retained_release_unverified");
      return;
    }
    // Durable single-use stopped authority BEFORE launch. A crash between these
    // writes can refuse a resume; it can never authorize a duplicate process.
    stopped.resumeCommandId = op.commandId;
    this.persist(stopped);
    op.target = stopped.target;
    op.fenceId = stopped.fenceId;
    op.exitProof = stopped.exitProof;
    op.observed ??= { ...op.expected, instanceId: randomUUID() };
    op.phase = "resume_requested";
    this.persist(op);
    try {
      await this.options.lifecycle.resumeOwned({
        target: op.target,
        instanceId: op.observed.instanceId,
        previousInstanceId: op.expected.instanceId,
        dataScope: op.expected.dataScope,
        commandId: op.commandId,
        signal: new AbortController().signal,
      });
      await this.exact(op.observed);
      delete op.startupFailure;
      op.status = "ready";
      op.phase = "ready";
      op.admissionSettlement = { state: "pending", updatedAt: Date.now() };
      this.persist(op);
      await this.settle(op, "resumed");
    } catch (error) {
      if (op.phase === "ready") {
        op.admissionSettlement = { state: "unknown", updatedAt: Date.now() };
        this.persist(op);
      } else {
        const startupFailure = startupFailureFrom(error);
        if (startupFailure) op.startupFailure = startupFailure;
        this.finish(
          op,
          "unknown",
          "resume_requested",
          "service_resume_unconfirmed",
        );
      }
    }
  }
  private async settle(op: ServiceRecord, outcome: "resumed" | "stop-refused") {
    const stopId = outcome === "resumed" ? op.stoppedCommandId! : op.commandId;
    try {
      const value = object(
        await this.options.host.releaseServiceStop({
          fenceId: op.fenceId!,
          commandId: stopId,
          outcome,
          ...(outcome === "resumed" ? { resumeCommandId: op.commandId } : {}),
          evidence: { serviceStopCommandId: stopId },
        }),
      );
      if (
        value.released !== true ||
        value.intakeClosed !== false ||
        value.purpose !== "service-stop" ||
        value.fenceId !== op.fenceId ||
        value.commandId !== stopId ||
        value.outcome !== outcome ||
        !sameService(value.expected, op.expected) ||
        !sameService(value.observed, op.observed ?? op.expected) ||
        (outcome === "resumed" && value.resumeCommandId !== op.commandId)
      )
        throw Error("service_release_unconfirmed");
      this.settlement(op, "settled");
    } catch {
      this.settlement(op, "unknown");
    }
  }
  private settlement(op: ServiceRecord, state: "settled" | "unknown") {
    const latest = this.store.read(op.commandId)!;
    // Concurrent passive reconciliation can already have completed the release.
    // A late lost reply must not downgrade that durable host confirmation.
    if (latest.admissionSettlement?.state === "settled" && state !== "settled")
      return;
    latest.admissionSettlement = { state, updatedAt: Date.now() };
    op.admissionSettlement = latest.admissionSettlement;
    this.persist(latest);
  }
  async reconcile(commandId: string): Promise<ServiceReceipt> {
    const op = this.store.read(token(commandId));
    if (!op) throw Error("unknown_command");
    if (this.active) return serviceReceipt(op);
    if (
      op.operation === "stop" &&
      op.status === "unknown" &&
      op.phase === "stop_requested"
    ) {
      const proof = this.options.lifecycle.processes.exitProof(op.expected);
      if (proof) {
        op.exitProof = proof;
        this.finish(op, "stopped", "stopped");
      }
    }
    if (
      ["resume","handoff"].includes(op.operation) &&
      op.status === "unknown" &&
      op.phase === "resume_requested" &&
      op.observed &&
      op.target
    ) {
      try {
        await this.exact(op.observed);
        if (
          !(await this.options.releases.verify(op.target, {
            commandId: op.commandId,
            signal: new AbortController().signal,
          }))
        )
          return serviceReceipt(op);
      } catch {
        return serviceReceipt(op);
      }
      delete op.startupFailure;
      op.status = "ready";
      op.phase = "ready";
      op.admissionSettlement = { state: "pending", updatedAt: Date.now() };
      this.persist(op);
    }
    if (
      ["resume","handoff"].includes(op.operation) &&
      op.status === "ready" &&
      op.admissionSettlement?.state !== "settled"
    ) {
      try {
        await this.exact(op.observed!);
      } catch {
        return serviceReceipt(op);
      }
      await this.settle(op, "resumed");
    }
    return this.receipt(commandId)!;
  }
  async close() {
    if (this.closing) return;
    this.closing = true;
    await this.active;
    this.store.close();
  }
}
/** Host obtains proof from its authenticated supervisor itself. Request evidence
 * is correlation only and never conveys process or release authority. */
export function createHostServiceReleaseVerifier(options: {
  service: {
    proof(
      commandId: string,
    ): ServiceReceipt | null | Promise<ServiceReceipt | null>;
  };
  inspectRunningService(): ServiceIdentity | Promise<ServiceIdentity>;
}) {
  return async (
    request: ServiceReleaseRequest,
  ): Promise<ServiceReleaseProof> => {
    if (!["resumed", "stop-refused"].includes(request.outcome))
      throw Error("service_proof_unconfirmed");
    const expected = serviceIdentity(request.serviceIdentity!),
      observed = serviceIdentity(await options.inspectRunningService());
    if (
      request.purpose !== "service-stop" ||
      request.instanceId !== expected.instanceId ||
      request.dataScope !== expected.dataScope ||
      !sameService({ ...observed, instanceId: expected.instanceId }, expected)
    )
      throw Error("service_proof_unconfirmed");
    const stop = await options.service.proof(token(request.commandId));
    if (
      !stop ||
      stop.operation !== "stop" ||
      stop.fenceId !== request.fenceId ||
      !sameService(stop.expected, expected)
    )
      throw Error("service_proof_unconfirmed");
    const common = {
      verified: true as const,
      kind: "service-lifecycle" as const,
      fenceId: request.fenceId,
      commandId: request.commandId,
      dataScope: expected.dataScope,
      instanceId: observed.instanceId,
      expected,
      observed,
    };
    if (request.outcome === "resumed") {
      const resumed = await options.service.proof(
        token(request.resumeCommandId),
      );
      if (
        stop.status !== "stopped" ||
        !stop.exitProof ||
        stop.exitProof.ownerId !== expected.ownerId ||
        stop.exitProof.instanceId !== expected.instanceId ||
        stop.resumeCommandId !== request.resumeCommandId ||
        !resumed ||
        !["resume","handoff"].includes(resumed.operation) ||
        resumed.status !== "ready" ||
        resumed.phase !== "ready" ||
        resumed.stoppedCommandId !== stop.commandId ||
        resumed.fenceId !== stop.fenceId ||
        !sameService(resumed.expected, expected) ||
        !resumed.observed ||
        !sameService(resumed.observed, observed) ||
        observed.instanceId === expected.instanceId
      )
        throw Error("service_proof_unconfirmed");
      return {
        ...common,
        outcome: "ready",
        serviceOutcome: "resumed",
        receiptId: resumed.commandId,
        resumeCommandId: resumed.commandId,
        exitReceiptId: token(stop.exitProof.ownerReceiptId),
        readyReceiptId: resumed.commandId,
      };
    }
    if (
      stop.status !== "refused" ||
      stop.phase !== "stop_refused" ||
      stop.noEffect !== true ||
      !sameService(expected, observed) ||
      request.resumeCommandId !== undefined
    )
      throw Error("service_proof_unconfirmed");
    return {
      ...common,
      outcome: "unchanged",
      serviceOutcome: "stop-refused",
      receiptId: stop.commandId,
      refusalReceiptId: stop.commandId,
    };
  };
}
