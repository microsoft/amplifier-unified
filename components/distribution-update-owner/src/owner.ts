import {
  parseReleaseNotes,
  noticeReview,
  noticeDigest,
  acceptedNoticeDigests,
  notesRevision,
  notesPage,
  PUBLISHED_RELEASES_URL,
  type NoticeReview,
  type ReleaseNotesQuery,
  type ReleaseNotesSummary,
  type ReviewedReleaseNotesEntry,
} from "./release-notes.js";
import { randomUUID } from "node:crypto";
import { Store } from "./store.js";
import { parseStartupFailure, startupFailureFrom, type StartupFailure } from "./startup-diagnostics.js";
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
  startupFailure?: StartupFailure;
  noticeReview?: NoticeReview;
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
  ...(parseStartupFailure(op.startupFailure) ? {startupFailure: parseStartupFailure(op.startupFailure)} : {}),
  ...(op.noticeReview ? { noticeReview: { ...op.noticeReview } } : {}),
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
  "staged_candidate_conflict",
  "staged_candidate_unavailable",
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
  private notesCache: {
    key: string;
    summary: ReleaseNotesSummary;
    entries: ReviewedReleaseNotesEntry[];
  } | null = null;

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
  /** Preparation is an explicit terminal action; it never closes app intake. */
  prepare(commandId: string, releaseId: string | null = null): Receipt {
    return this.submit(commandId, "prepare", {
      releaseId: releaseId === null ? null : token(releaseId, 100),
    });
  }
  activate(commandId: string, value: {
    preparedCommandId: string; targetDigest: string; expectedCurrentId: string | null;
  }): Receipt {
    if (!value || !/^[a-f0-9]{64}$/.test(value.targetDigest)) throw Error("invalid_release_proof");
    return this.submit(commandId, "activate", {
      preparedCommandId: token(value.preparedCommandId), targetDigest: value.targetDigest,
      expectedCurrentId: value.expectedCurrentId === null ? null : token(value.expectedCurrentId, 100),
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
  /** Local publication metadata only. Never joins the install queue or waits for a poll. */
  reviewNotice(commandId: string, value: NoticeReview): Receipt {
    if (this.closing) throw Error("owner_closed");
    const review = noticeReview(value),
      now = Date.now();
    let fresh = false;
    const op = this.store.transaction(() => {
      const accepted = this.store.accept({
        id: token(commandId),
        command: "review-notice",
        args: { ...review },
        status: "succeeded",
        phase: "notice_reviewed",
        createdAt: now,
        updatedAt: now,
        noticeReview: review,
      });
      if (!accepted.fresh) return accepted.operation;
      fresh = true;
      const found = this.store
        .notes()
        .publication.entries.find((e) => e.version === review.version)
        ?.notices.find((n) => n.id === review.noticeId);
      if (
        !found ||
        noticeDigest(review.version, found) !== review.contentDigest
      ) {
        accepted.operation.status = "failed";
        accepted.operation.phase = "notice_review_refused";
        accepted.operation.errorCode = found
          ? "notice_changed"
          : "notice_not_found";
        this.store.write(accepted.operation);
      } else this.store.saveReview(review, accepted.operation);
      return accepted.operation;
    });
    if (fresh) this.record(op);
    return receipt(op);
  }
  private notesView() {
    const state = this.store.state();
    const currentVersion = state.current?.identity.version ?? null;
    const recommendedVersion =
      state.catalog?.releases.find((r) => r.id === state.catalog?.recommendedId)
        ?.version ?? null;
    const key = JSON.stringify([
      this.store.notesRevision(),
      currentVersion,
      recommendedVersion,
    ]);
    if (this.notesCache?.key === key) return this.notesCache;
    const saved = this.store.notes(),
      reviews = this.store.reviews();
    const entries: ReviewedReleaseNotesEntry[] = saved.publication.entries.map(
      (e) => ({
        ...e,
        notices: e.notices.map((n) => {
          const reviewed = acceptedNoticeDigests(e.version, n)
            .map((d) => reviews.get(d))
            .find(Boolean);
          return {
            id: n.id,
            title: n.title,
            detail: n.detail,
            action: n.action,
            contentDigest: noticeDigest(e.version, n),
            reviewedAt: reviewed?.reviewedAt ?? null,
            reviewReceiptId: reviewed?.reviewReceiptId ?? null,
          };
        }),
      }),
    );
    const summary: ReleaseNotesSummary = {
      schema: "distribution-release-notes-v1",
      revision: notesRevision([key, entries]),
      currentVersion,
      recommendedVersion,
      publishedReleasesUrl: PUBLISHED_RELEASES_URL,
      warning: saved.warning,
      totalEntries: entries.length,
      unreviewedCount: entries.reduce(
        (count, e) =>
          count + e.notices.filter((n) => n.reviewedAt === null).length,
        0,
      ),
    };
    return (this.notesCache = { key, summary, entries });
  }
  releaseNotes(query: ReleaseNotesQuery = {}) {
    const view = this.notesView();
    return notesPage(view.summary, view.entries, query);
  }
  /** Bootstrap offline notes from the exact signed installed receipt, once.
   * Startup never crawls release history or invokes a network check for notes. */
  async loadInstalledReleaseNotes(): Promise<void> {
    const current = this.store.state().current;
    if (
      this.store.notesRevision() !== "empty" ||
      !current ||
      !this.options.releases.notes
    )
      return;
    try {
      const data = await this.options.releases.notes(current);
      if (!this.closing && this.store.notesRevision() === "empty")
        this.saveReleaseNotes(data);
    } catch {
      /* Optional editorial data cannot prevent the supervisor from opening. */
    }
  }
  private saveReleaseNotes(data: {
    releaseNotes?: unknown;
    releaseNotesWarning?: unknown;
  }): void {
    let publication;
    let warning: import("./release-notes.js").ReleaseNotesWarning =
      "release_notes_unavailable";
    if (data.releaseNotes !== undefined) {
      try {
        publication = parseReleaseNotes(data.releaseNotes);
        warning = null;
      } catch {
        warning = "release_notes_invalid";
      }
    } else if (data.releaseNotesWarning === "release_notes_invalid")
      warning = "release_notes_invalid";
    this.store.saveNotes(
      publication,
      warning,
      this.store.state().current?.identity.version ?? null,
    );
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
          (op) => ["install", "rollback", "prepare", "activate"].includes(op.command) && !terminal(op),
        )
    );
  }
  /** Private authenticated supervisor evidence, separate from shareable diagnostics.
   * Opaque identities bind the durable receipt to a host fence and actual launch. */
  restartProof(commandId: string) {
    const op = this.store.read(token(commandId));
    if (!op || !["install", "rollback", "activate"].includes(op.command)) return null;
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
      staged: state.staged ? {
        commandId: state.staged.commandId, target: identity(state.staged.target.identity),
        expectedCurrentId: state.staged.expectedCurrentId,
      } : null,
      catalog: state.catalog,
      releaseNotes: { ...this.notesView().summary },
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
        ...(parseStartupFailure(op.startupFailure) ? {startupFailure: parseStartupFailure(op.startupFailure)} : {}),
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
          (op) => ["install", "rollback", "prepare", "activate"].includes(op.command) && !terminal(op),
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
        const checked = await this.options.releases.check({
          ...this.context(op),
          fresh: op.args.fresh === true,
        });
        const result = catalog(checked);
        this.controller.signal.throwIfAborted();
        const state = this.store.state();
        this.saveReleaseNotes(checked);
        state.catalog = result;
        state.lastCheck = Date.now();
        state.lastCheckSucceeded = true;
        this.store.save(state);
        this.finish(op, "succeeded", "checked");
        const hasInstall = this.store
          .pending()
          .some(
            (row) =>
              ["install", "rollback", "prepare", "activate"].includes(row.command) &&
              row.status !== "unknown",
          );
        if (
          state.preferences.autoInstall &&
          !state.staged &&
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
              ["install", "rollback", "prepare", "activate"].includes(row.command),
          )
      )
        throw Error("mutation_pending");
      let state = this.store.state();
      if (op.command === "activate") {
        const staged = state.staged;
        const preparation = this.store.read(String(op.args.preparedCommandId));
        if (!staged || !preparation || preparation.command !== "prepare" ||
            preparation.status !== "succeeded" || preparation.phase !== "prepared")
          throw Error("staged_candidate_unavailable");
        if (staged.commandId !== op.args.preparedCommandId ||
            staged.target.identity.digest !== op.args.targetDigest ||
            staged.expectedCurrentId !== op.args.expectedCurrentId ||
            (state.current?.identity.id ?? null) !== op.args.expectedCurrentId ||
            !same(preparation.target?.identity, staged.target.identity))
          throw Error("staged_candidate_conflict");
        op.target = prepared(staged.target);
      } else if (op.command === "rollback") {
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
      if (op.command === "prepare") {
        // One bounded staged slot persists independently of process ownership.
        // Automatic installation waits for an explicit decision while staged;
        // manual install remains direct and may supersede this candidate.
        state.staged = {commandId: op.id, target: op.target!, expectedCurrentId: state.current?.identity.id ?? null};
        op.status = "succeeded"; op.phase = "prepared"; op.updatedAt = Date.now();
        this.store.commit(op, state); this.record(op); this.resolve(op);
        return;
      }
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
          ["install", "activate"].includes(op.command) &&
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
      // Keep the diagnostic beside the durable uncertain effect. Knowing why a
      // child failed never authorizes replay, clears admission or promotes it.
      const startupFailure = startupFailureFrom(error);
      if (startupFailure) op.startupFailure = startupFailure;
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
    delete state.staged;
    if (op.command === "rollback") state.preferences.autoInstall = false;
    op.status = "succeeded";
    op.phase = "ready";
    op.updatedAt = Date.now();
    delete op.errorCode;
    delete op.startupFailure;
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
