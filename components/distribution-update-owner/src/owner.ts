import { randomUUID } from "node:crypto";
import { Store } from "./store.js";
import {
  catalog,
  identity,
  prepared,
  preferences,
  same,
  token,
  type Command,
  type LifecyclePort,
  type NativeGenerationPort,
  type Operation,
  type Preferences,
  type PreparedRelease,
  type ReleasePort,
} from "./types.js";

export interface Receipt {
  id: string;
  command: Command;
  status: Operation["status"];
  phase: string;
  createdAt: number;
  updatedAt: number;
  target?: ReturnType<typeof identity>;
  errorCode?: string;
  admission?: { activeWork: 0; intakeClosed: true; observedAt: number };
  activation?: { startedAt: number; completedAt?: number };
  admissionSettlement?: Operation["admissionSettlement"];
}
export interface OwnerOptions {
  directory: string;
  dataScope: string;
  releases: ReleasePort;
  lifecycle: LifecyclePort;
  native?: NativeGenerationPort;
  /** An already qualified baseline, never inferred from a catalog entry. */
  initial?: PreparedRelease;
  preferences?: Preferences;
  /** Notification only; exceptions cannot alter a committed operation. */
  onChange?: (receipt: Receipt) => void | Promise<void>;
  /** Trusted service owner may retain the process for explicit stop/resume. */
  mutationBlocked?: () => boolean;
}
const defaults: Preferences = {
  autoCheck: true,
  autoInstall: false,
  intervalMs: 14400000,
};
const terminal = (op: Operation) =>
  ["succeeded", "failed", "unknown"].includes(op.status);
const observableCompletion = (op: Operation) =>
  terminal(op) && op.admissionSettlement?.state !== "pending";
const receipt = (op: Operation): Receipt => ({
  id: op.id,
  command: op.command,
  status: op.status,
  phase: op.phase,
  createdAt: op.createdAt,
  updatedAt: op.updatedAt,
  ...(op.target ? { target: identity(op.target.identity) } : {}),
  ...(op.errorCode ? { errorCode: op.errorCode } : {}),
  ...(op.activation ? { activation: { ...op.activation } } : {}),
  ...(op.admissionSettlement
    ? { admissionSettlement: { ...op.admissionSettlement } }
    : {}),
  ...(op.admission
    ? {
        admission: {
          activeWork: op.admission.activeWork,
          intakeClosed: op.admission.intakeClosed,
          observedAt: op.admission.observedAt,
        },
      }
    : {}),
});
const knownErrors = new Set([
  "catalog_limit",
  "duplicate_release",
  "unknown_recommendation",
  "invalid_identifier",
  "invalid_version",
  "invalid_release_proof",
  "candidate_mismatch",
  "candidate_unverified",
  "check_required",
  "release_not_found",
  "restart_unresolved",
  "mutation_pending",
  "rollback_conflict",
  "rollback_unavailable",
  "running_identity_mismatch",
  "admission_unproven",
  "channel_untrusted",
  "channel_expired_or_invalid",
  "channel_invalid",
  "source_preserved",
  "source_advanced",
  "release_superseded",
  "local_source_changes",
  "artifact_digest_mismatch",
  "artifact_origin_denied",
  "archive_inventory_mismatch",
  "candidate_inventory_mismatch",
  "release_platform_mismatch",
  "release_fetch_failed",
  "download_limit",
]);

/** Distribution authority only. This object never registers host actions or
 * writes native-generation state. Public receipts are safe, bounded projections;
 * the private ledger also retains opaque candidate handles for reconciliation. */
export class DistributionUpdateOwner {
  private store: Store;
  private queue: string[] = [];
  private pumping: Promise<void> | null = null;
  private controller = new AbortController();
  private closing = false;
  private started = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private waiters = new Map<string, Set<(value: Receipt) => void>>();
  private idleRevision = 0;
  private readonly dataScope: string;

  constructor(private readonly options: OwnerOptions) {
    this.dataScope = token(options.dataScope);
    this.store = new Store(options.directory, {
      schema: 1,
      dataScope: this.dataScope,
      current: options.initial ? prepared(options.initial) : null,
      previous: null,
      catalog: null,
      lastCheck: 0,
      lastCheckSucceeded: false,
      preferences: preferences(options.preferences ?? defaults),
    });
    // Recovery only labels interrupted commands. Do not enqueue them, even if
    // their release still appears in a fresh catalog: effects may already exist.
  }

  check(commandId: string, fresh = true): Receipt {
    if (typeof fresh !== "boolean") throw Error("invalid_fresh");
    return this.submit(commandId, "check", { fresh });
  }
  install(commandId: string, releaseId: string | null = null): Receipt {
    return this.submit(commandId, "install", {
      releaseId: releaseId === null ? null : token(releaseId, 100),
    });
  }
  rollback(commandId: string, expectedCurrentId: string): Receipt {
    return this.submit(commandId, "rollback", {
      expectedCurrentId: token(expectedCurrentId, 100),
    });
  }
  setPreferences(commandId: string, value: Preferences): Receipt {
    return this.submit(commandId, "preferences", { ...preferences(value) });
  }
  receipt(commandId: string): Receipt | null {
    const op = this.store.read(token(commandId));
    return op ? receipt(op) : null;
  }
  /** Trusted local supervisor composition only; never projected through RPC. */
  qualifiedCurrent(): PreparedRelease | null {
    const value = this.store.state().current;
    return value ? prepared(value) : null;
  }
  /** Trusted service composition; excludes the reciprocal service guard. */
  blocksServiceStop(): boolean {
    return (
      this.unresolvedRestart() ||
      this.store
        .pending()
        .some(
          (op) => ["install", "rollback"].includes(op.command) && !terminal(op),
        )
    );
  }
  /** Private authenticated supervisor evidence, separate from shareable diagnostics.
   * Opaque identities bind the durable receipt to a host fence and actual launch. */
  restartProof(commandId: string) {
    const op = this.store.read(token(commandId));
    if (!op || !["install", "rollback"].includes(op.command)) return null;
    return {
      schema: "distribution-restart-proof-v1" as const,
      commandId: op.id,
      purpose: "distribution-update" as const,
      dataScope: this.dataScope,
      status: op.status,
      phase: op.phase,
      target: op.target ? identity(op.target.identity) : null,
      instanceId: op.instanceId ?? null,
      previousInstanceId: op.previousInstanceId ?? null,
      admission: op.admission ? structuredClone(op.admission) : null,
      admittedRunning: op.admittedRunning
        ? structuredClone(op.admittedRunning)
        : null,
    };
  }
  waitFor(commandId: string): Promise<Receipt> {
    const op = this.store.read(token(commandId));
    if (!op) throw Error("unknown_command");
    if (observableCompletion(op)) return Promise.resolve(receipt(op));
    return new Promise((resolve) => {
      const callbacks = this.waiters.get(commandId) ?? new Set();
      callbacks.add(resolve);
      this.waiters.set(commandId, callbacks);
    });
  }
  /** Explicitly start background scheduling; construction and inspection are passive. */
  start(): void {
    if (this.closing) throw Error("owner_closed");
    this.started = true;
    this.schedule();
  }
  /** Host idle events wake admission immediately; there is no idle polling loop. */
  notifyIdle(): void {
    this.idleRevision++;
    if (this.closing) return;
    for (const op of this.store.pending())
      if (
        op.status === "waiting" &&
        op.phase === "waiting_idle" &&
        !this.queue.includes(op.id)
      )
        this.queue.push(op.id);
    this.kick();
  }
  inspect() {
    const state = this.store.state();
    return {
      current: state.current?.identity ?? null,
      previous: state.previous?.identity ?? null,
      catalog: state.catalog,
      lastCheck: state.lastCheck,
      lastCheckSucceeded: state.lastCheckSucceeded,
      preferences: state.preferences,
      operations: this.store.recent(50).map(receipt),
      restartUnresolved: this.unresolvedRestart(),
      actionReadiness: this.actionReadiness(),
    };
  }
  diagnostics() {
    return {
      schema: "distribution-update-diagnostics-v1",
      capturedAt: Date.now(),
      ...this.inspect(),
      events: this.store.events(),
    };
  }
  async inspectRunning() {
    const value = await this.options.lifecycle.inspect();
    if (!value) return null;
    if (typeof value.ready !== "boolean") throw Error("invalid_readiness");
    return {
      identity: identity(value.identity),
      instanceId: token(value.instanceId),
      dataScope: token(value.dataScope),
      ready: value.ready,
    };
  }
  async inspectNative() {
    if (!this.options.native) return null;
    const value = await this.options.native.inspect();
    for (const count of [value.activeWorkers, value.pendingWorkers])
      if (!Number.isSafeInteger(count) || count < 0)
        throw Error("invalid_native_status");
    return {
      current: value.current === null ? null : token(value.current),
      previous: value.previous === null ? null : token(value.previous),
      activeWorkers: value.activeWorkers,
      pendingWorkers: value.pendingWorkers,
    };
  }

  /** Read-only reconciliation can acknowledge a restart already observed. It
   * never prepares, launches, rolls back, or replays conversation work. */
  async reconcile(commandId: string): Promise<Receipt> {
    const op = this.store.read(token(commandId));
    if (!op) throw Error("unknown_command");
    if (op.status === "succeeded" && op.phase === "ready") {
      await this.reconcileFence(op);
      return this.receipt(op.id)!;
    }
    if (op.status === "failed" && op.phase === "pre_restart_refused") {
      await this.reconcileFence(op, "unchanged");
      return this.receipt(op.id)!;
    }
    if (
      op.status !== "unknown" ||
      op.phase !== "restart_requested" ||
      !op.target
    )
      return receipt(op);
    const observed = await this.options.lifecycle.inspect();
    if (!this.matches(op, observed)) return receipt(op);
    if (!(await this.options.releases.verify(op.target, this.context(op))))
      return receipt(op);
    // Re-read after external calls, so a concurrent reconciliation is idempotent.
    const latest = this.store.read(op.id)!;
    if (latest.status === "unknown" && latest.phase === "restart_requested")
      this.promoted(latest);
    await this.reconcileFence(this.store.read(op.id)!);
    return this.receipt(op.id)!;
  }
  private async reconcileFence(
    op: Operation,
    outcome: "ready" | "unchanged" = "ready",
  ): Promise<void> {
    if (op.admissionSettlement?.state === "settled") return;
    if (!op.admissionSettlement && !op.admission) return;
    if (!this.options.lifecycle.reconcileAdmission) {
      this.settlement(op, "unknown", outcome);
      return;
    }
    const observed = await this.options.lifecycle.inspect();
    const unchanged =
      observed?.ready &&
      op.admittedRunning &&
      !op.instanceId &&
      observed.instanceId === op.admittedRunning.instanceId &&
      observed.dataScope === this.dataScope &&
      op.admittedRunning.dataScope === this.dataScope &&
      same(observed.identity, op.admittedRunning.identity);
    if (!(outcome === "ready" ? this.matches(op, observed) : unchanged))
      throw Error("readiness_unconfirmed");
    try {
      await this.options.lifecycle.reconcileAdmission({
        commandId: op.id,
        purpose: "distribution-update",
        dataScope: this.dataScope,
        outcome,
        observed: observed!,
      });
      this.settlement(op, "settled", outcome);
    } catch {
      this.settlement(op, "unknown", outcome);
    }
  }

  async close(): Promise<void> {
    if (this.closing) return;
    this.closing = true;
    this.started = false;
    if (this.timer) clearTimeout(this.timer);
    this.controller.abort(new Error("owner_closed"));
    await this.pumping;
    for (const op of this.store.pending())
      if (op.status !== "unknown") {
        if (op.command === "check") {
          const state = this.store.state();
          state.catalog = null;
          state.lastCheckSucceeded = false;
          this.store.save(state);
        }
        this.finish(op, "unknown", op.phase, "owner_interrupted");
      }
    this.store.close();
  }

  private submit(
    id: string,
    command: Command,
    args: Record<string, unknown>,
  ): Receipt {
    if (this.closing) throw Error("owner_closed");
    const now = Date.now(),
      accepted = this.store.accept({
        id: token(id),
        command,
        args,
        status: "queued",
        phase: "accepted",
        createdAt: now,
        updatedAt: now,
      });
    if (accepted.fresh) {
      this.record(accepted.operation);
      this.queue.push(id);
      this.kick();
    }
    return receipt(accepted.operation);
  }
  private kick(): void {
    if (this.pumping || this.closing) return;
    // A manual request supersedes the scheduled wake, including a due timer
    // whose callback has not run yet. Do not queue a redundant background check.
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    // Manual actions start in this microtask, never on the background timer.
    this.pumping = Promise.resolve()
      .then(async () => {
        while (this.queue.length && !this.closing) {
          const op = this.store.read(this.queue.shift()!);
          if (op && !terminal(op)) await this.execute(op);
        }
      })
      .finally(() => {
        this.pumping = null;
        if (this.queue.length && !this.closing) this.kick();
        else this.schedule();
      });
  }
  private context(op: Operation) {
    return { commandId: op.id, signal: this.controller.signal };
  }
  private record(op: Operation): void {
    // Receipts are authoritative. A telemetry failure must not turn an already
    // committed promotion into a reported failure and invite duplicate work.
    try {
      this.store.event({
        id: op.id,
        command: op.command,
        status: op.status,
        phase: op.phase,
        at: op.updatedAt,
        ...(op.errorCode ? { errorCode: op.errorCode } : {}),
      });
    } catch {}
    try {
      Promise.resolve(this.options.onChange?.(receipt(op))).catch(() => {});
    } catch {
      /* A subscriber does not own update truth. */
    }
  }
  private phase(op: Operation, phase: string): void {
    op.status = "running";
    op.phase = phase;
    op.updatedAt = Date.now();
    delete op.errorCode;
    this.store.write(op);
    this.record(op);
  }
  private finish(
    op: Operation,
    status: Operation["status"],
    phase: string,
    errorCode?: string,
  ): void {
    op.status = status;
    op.phase = phase;
    op.updatedAt = Date.now();
    if (errorCode) op.errorCode = errorCode;
    else delete op.errorCode;
    this.store.write(op);
    this.record(op);
    this.resolve(op);
  }
  private resolve(op: Operation): void {
    if (!observableCompletion(op)) return;
    for (const callback of this.waiters.get(op.id) ?? []) callback(receipt(op));
    this.waiters.delete(op.id);
  }
  private unresolvedRestart(except?: string): boolean {
    return (
      this.store.unsettledAdmissions().some((op) => op.id !== except) ||
      this.store
        .pending()
        .some(
          (op) =>
            op.id !== except &&
            op.status === "unknown" &&
            [
              "restart_requested",
              "admission_requested",
              "admitted",
              "qualifying_activation",
              "activation_qualified",
            ].includes(op.phase),
        )
    );
  }
  private actionReadiness(): {
    state: "available" | "busy" | "reconciliation_required";
    commandId?: string;
  } {
    if (this.options.mutationBlocked?.())
      return { state: "reconciliation_required" };
    const unsettled = this.store.unsettledAdmissions();
    const unknown =
      unsettled.find((op) => op.admissionSettlement?.state === "unknown") ??
      this.store
        .pending()
        .find(
          (op) =>
            op.status === "unknown" &&
            [
              "restart_requested",
              "admission_requested",
              "admitted",
              "qualifying_activation",
              "activation_qualified",
            ].includes(op.phase),
        );
    if (unknown)
      return { state: "reconciliation_required", commandId: unknown.id };
    const busy =
      unsettled[0] ??
      this.store
        .pending()
        .find(
          (op) => ["install", "rollback"].includes(op.command) && !terminal(op),
        );
    return busy
      ? { state: "busy", commandId: busy.id }
      : { state: "available" };
  }
  private settlement(
    op: Operation,
    state: "pending" | "settled" | "unknown",
    outcome: "ready" | "unchanged" | "unknown",
  ) {
    const latest = this.store.read(op.id)!;
    // A racing reconciliation may already have verified release. Never replace
    // that durable proof with a late lost response from the original request.
    if (latest.admissionSettlement?.state === "settled" && state !== "settled")
      return;
    latest.admissionSettlement = { state, outcome, updatedAt: Date.now() };
    latest.updatedAt = Date.now();
    op.admissionSettlement = latest.admissionSettlement;
    this.store.write(latest);
    this.record(latest);
    this.resolve(latest);
  }
  private async execute(op: Operation): Promise<void> {
    try {
      this.controller.signal.throwIfAborted();
      if (op.command === "preferences") {
        const state = this.store.state();
        state.preferences = preferences(op.args as unknown as Preferences);
        this.store.save(state);
        this.finish(op, "succeeded", "preferences_saved");
        return;
      }
      if (op.command === "check") {
        this.phase(op, "checking");
        const result = catalog(
          await this.options.releases.check({
            ...this.context(op),
            fresh: op.args.fresh === true,
          }),
        );
        this.controller.signal.throwIfAborted();
        const state = this.store.state();
        state.catalog = result;
        state.lastCheck = Date.now();
        state.lastCheckSucceeded = true;
        this.store.save(state);
        this.finish(op, "succeeded", "checked");
        const hasInstall = this.store
          .pending()
          .some(
            (row) =>
              ["install", "rollback"].includes(row.command) &&
              row.status !== "unknown",
          );
        if (
          state.preferences.autoInstall &&
          result.recommendedId &&
          result.recommendedId !== state.current?.identity.id &&
          !hasInstall &&
          !this.unresolvedRestart()
        ) {
          this.install(
            `automatic-install:${randomUUID()}`,
            result.recommendedId,
          );
        }
        return;
      }
      if (this.options.mutationBlocked?.() || this.unresolvedRestart(op.id))
        throw Error("restart_unresolved");
      if (
        this.store
          .pending()
          .some(
            (row) =>
              row.id !== op.id &&
              row.status === "waiting" &&
              ["install", "rollback"].includes(row.command),
          )
      )
        throw Error("mutation_pending");
      let state = this.store.state();
      if (op.command === "rollback") {
        if (state.current?.identity.id !== op.args.expectedCurrentId)
          throw Error("rollback_conflict");
        if (!state.previous) throw Error("rollback_unavailable");
        op.target = prepared(state.previous);
        // Rollback expresses deliberate preference for the older release.
        state.preferences.autoInstall = false;
        this.store.save(state);
      } else if (!op.target) {
        if (!state.lastCheckSucceeded || !state.catalog)
          throw Error("check_required");
        const id = op.args.releaseId ?? state.catalog.recommendedId;
        const release = state.catalog.releases.find((row) => row.id === id);
        if (!release) throw Error("release_not_found");
        if (same(state.current?.identity, release)) {
          this.finish(op, "succeeded", "already_current");
          return;
        }
        this.phase(op, "preparing");
        op.target = prepared(
          await this.options.releases.prepare(release, this.context(op)),
        );
        if (!same(op.target.identity, release))
          throw Error("candidate_mismatch");
      }
      this.phase(op, "verifying");
      if (!(await this.options.releases.verify(op.target!, this.context(op))))
        throw Error("candidate_unverified");
      this.controller.signal.throwIfAborted();
      const revision = this.idleRevision;
      // Admission can close a durable external gate even if its reply is lost.
      // Record uncertainty before the call and never retry an unknown admission.
      op.admissionSettlement = {
        state: "pending",
        outcome: "unknown",
        updatedAt: Date.now(),
      };
      this.phase(op, "admission_requested");
      const lease = await this.options.lifecycle.admitRestart({
        ...this.context(op),
        purpose: "distribution-update",
        dataScope: this.dataScope,
      });
      if (!lease) {
        delete op.admissionSettlement;
        this.finish(op, "waiting", "waiting_idle");
        // Avoid losing an idle notification that raced with admission.
        if (revision !== this.idleRevision && !this.queue.includes(op.id))
          this.queue.push(op.id);
        return;
      }
      try {
        this.phase(op, "admitted");
        this.controller.signal.throwIfAborted();
        const running = await this.options.lifecycle.inspect();
        if (running)
          op.admittedRunning = {
            identity: identity(running.identity),
            instanceId: token(running.instanceId),
            dataScope: token(running.dataScope),
            ready: running.ready,
          };
        const evidence = lease.evidence;
        if (
          !evidence ||
          evidence.activeWork !== 0 ||
          evidence.intakeClosed !== true ||
          evidence.dataScope !== this.dataScope ||
          evidence.instanceId !== (running?.instanceId ?? null) ||
          !Number.isSafeInteger(evidence.observedAt) ||
          evidence.observedAt < 0
        )
          throw Error("admission_unproven");
        op.admission = {
          ...(evidence.fenceId ? { fenceId: token(evidence.fenceId) } : {}),
          activeWork: 0,
          intakeClosed: true,
          dataScope: this.dataScope,
          instanceId: running?.instanceId ?? null,
          observedAt: evidence.observedAt,
        };
        state = this.store.state();
        if (
          state.current &&
          (!running ||
            !same(state.current.identity, running.identity) ||
            running.dataScope !== this.dataScope)
        )
          throw Error("running_identity_mismatch");
        if (
          op.command === "install" &&
          this.options.releases.qualifyActivation
        ) {
          // Preparation can precede hours of active work. Observe source currency
          // again under held admission; verifying cached bytes cannot do this.
          // Keep rollback offline and do not re-prepare or rewrite old receipts.
          op.activation = { startedAt: Date.now() };
          this.phase(op, "qualifying_activation");
          await this.options.releases.qualifyActivation(
            op.target!,
            this.context(op),
          );
          this.controller.signal.throwIfAborted();
          op.activation.completedAt = Date.now();
          this.phase(op, "activation_qualified");
        }
        op.previous = state.current;
        op.previousInstanceId = running?.instanceId ?? null;
        op.instanceId = randomUUID();
        // Durable uncertainty BEFORE the external effect. Never infer success
        // from package presence or retry this call after a lost response.
        this.phase(op, "restart_requested");
        await this.options.lifecycle.restart({
          ...this.context(op),
          target: op.target!,
          instanceId: op.instanceId,
          dataScope: this.dataScope,
          previousInstanceId: op.previousInstanceId,
        });
        const observed = await this.options.lifecycle.inspect();
        if (!this.matches(op, observed)) {
          this.finish(
            op,
            "unknown",
            "restart_requested",
            "readiness_unconfirmed",
          );
          return;
        }
        this.promoted(op);
      } catch (error) {
        if (op.phase !== "restart_requested") {
          // The host verifier reads this durable no-restart receipt. Persist it
          // BEFORE lease.release('unchanged'); caller assertions are not proof.
          const code =
            error instanceof Error && knownErrors.has(error.message)
              ? error.message
              : "adapter_failed";
          this.finish(op, "failed", "pre_restart_refused", code);
          return;
        }
        throw error;
      } finally {
        // An admission adapter must itself remain safe if the restarted process
        // is uncertain. Cleanup failure cannot overwrite observed promotion.
        const outcome =
          op.status === "succeeded" && op.phase === "ready"
            ? "ready"
            : op.phase === "restart_requested"
              ? "unknown"
              : "unchanged";
        try {
          this.settlement(op, "pending", outcome);
          await lease.release(outcome);
          this.settlement(
            op,
            outcome === "unknown" ? "unknown" : "settled",
            outcome,
          );
        } catch {
          this.settlement(op, "unknown", outcome);
          try {
            this.store.event({
              id: op.id,
              phase: "admission_release_failed",
              at: Date.now(),
            });
          } catch {}
        }
      }
    } catch (error) {
      if (op.command === "check") {
        const state = this.store.state();
        state.lastCheckSucceeded = false;
        state.catalog = null;
        state.lastCheck = Date.now();
        this.store.save(state);
      }
      const uncertain =
        ["restart_requested", "admission_requested"].includes(op.phase) ||
        this.controller.signal.aborted;
      const code = uncertain
        ? "effect_unconfirmed"
        : error instanceof Error && knownErrors.has(error.message)
          ? error.message
          : "adapter_failed";
      if (op.admissionSettlement?.state === "pending")
        op.admissionSettlement = {
          ...op.admissionSettlement,
          state: "unknown",
          updatedAt: Date.now(),
        };
      this.finish(op, uncertain ? "unknown" : "failed", op.phase, code);
    }
  }
  private matches(
    op: Operation,
    running: Awaited<ReturnType<LifecyclePort["inspect"]>>,
  ): boolean {
    return (
      !!running &&
      running.ready === true &&
      running.instanceId === op.instanceId &&
      running.instanceId !== op.previousInstanceId &&
      running.dataScope === this.dataScope &&
      same(running.identity, op.target?.identity)
    );
  }
  private promoted(op: Operation): void {
    const state = this.store.state();
    state.previous = op.previous ?? null;
    state.current = op.target!;
    if (op.command === "rollback") state.preferences.autoInstall = false;
    op.status = "succeeded";
    op.phase = "ready";
    op.updatedAt = Date.now();
    delete op.errorCode;
    this.store.commit(op, state);
    this.record(op);
    this.resolve(op);
  }
  private schedule(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (
      !this.started ||
      this.closing ||
      this.pumping ||
      this.unresolvedRestart()
    )
      return;
    const state = this.store.state();
    if (!state.preferences.autoCheck) return;
    const delay = Math.max(
      0,
      state.lastCheck + state.preferences.intervalMs - Date.now(),
    );
    this.timer = setTimeout(() => {
      this.timer = null;
      this.check(`automatic-check:${randomUUID()}`, false);
    }, delay);
    this.timer.unref();
  }
}
