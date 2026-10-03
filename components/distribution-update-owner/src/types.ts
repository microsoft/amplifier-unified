/** All paths, credentials and execution authority belong to trusted adapters. */
export interface ReleaseIdentity {
  id: string;
  version: string;
  revision: string;
  digest: string;
}
export interface Catalog {
  releases: ReleaseIdentity[];
  recommendedId: string | null;
}
export interface PreparedRelease {
  identity: ReleaseIdentity;
  handle: string;
}
export interface RunningIdentity {
  identity: ReleaseIdentity;
  instanceId: string;
  dataScope: string;
  ready: boolean;
}
export interface OperationContext {
  commandId: string;
  signal: AbortSignal;
}
export interface ReleasePort {
  check(context: OperationContext & { fresh: boolean }): Promise<Catalog>;
  /** Inactive candidate only. Must preserve explicit pins and local source edits. */
  prepare(
    release: ReleaseIdentity,
    context: OperationContext,
  ): Promise<PreparedRelease>;
  /** Read-only verification of exact artifact, dependencies and source provenance. */
  verify(
    prepared: PreparedRelease,
    context: OperationContext,
  ): Promise<boolean>;
  /** Read-only fresh source qualification after held admission, immediately
   * before forward activation. Never called for retained-byte rollback. */
  qualifyActivation?(
    prepared: PreparedRelease,
    context: OperationContext,
  ): Promise<void>;
}
export interface RestartRequest extends OperationContext {
  target: PreparedRelease;
  instanceId: string;
  dataScope: string;
  previousInstanceId: string | null;
}
export interface AdmissionEvidence {
  /** Durable public host fence identity when using external host control. */
  fenceId?: string;
  /** Held continuously by the lease; a sampled idle status is insufficient. */
  activeWork: 0;
  intakeClosed: true;
  instanceId: string | null;
  dataScope: string;
  observedAt: number;
}
export interface AdmissionLease {
  evidence: AdmissionEvidence;
  /** Unknown must retain a host fence until passive reconciliation succeeds. */
  release(outcome: "ready" | "unchanged" | "unknown"): void | Promise<void>;
}
export interface RestartAdmissionContext extends OperationContext {
  purpose: "distribution-update";
  dataScope: string;
}
export interface AdmissionReconciliation {
  commandId: string;
  purpose: "distribution-update";
  dataScope: string;
  outcome: "ready" | "unchanged";
  observed: RunningIdentity;
}
export interface LifecyclePort {
  /** No cold start or mutation. Read from an authenticated, owner-bound peer. */
  inspect(): Promise<RunningIdentity | null>;
  /** Atomically check active work AND close intake until lease.release(). */
  admitRestart(
    context?: RestartAdmissionContext,
  ): Promise<AdmissionLease | null>;
  /** Idempotently reconcile a durable host fence after a ready receipt exists. */
  reconcileAdmission?(request: AdmissionReconciliation): Promise<void>;
  /** May have effects even when it throws; never automatically retry. */
  restart(request: RestartRequest): Promise<void>;
}
export interface NativeGenerationStatus {
  current: string | null;
  previous: string | null;
  activeWorkers: number;
  pendingWorkers: number;
}
export interface NativeGenerationPort {
  /** Passive public native-owner inspection. No session creation or source scanning. */
  inspect(): Promise<NativeGenerationStatus>;
}
export interface Preferences {
  autoCheck: boolean;
  autoInstall: boolean;
  intervalMs: number;
}
export type Command = "check" | "install" | "rollback" | "preferences";
export type Status =
  | "queued"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "unknown";
export interface Operation {
  id: string;
  command: Command;
  status: Status;
  phase: string;
  createdAt: number;
  updatedAt: number;
  args: Record<string, unknown>;
  target?: PreparedRelease;
  instanceId?: string;
  previousInstanceId?: string | null;
  previous?: PreparedRelease | null;
  errorCode?: string;
  admission?: AdmissionEvidence;
  admittedRunning?: RunningIdentity;
  activation?: { startedAt: number; completedAt?: number };
  /** App readiness and host intake settlement are separate durable facts. */
  admissionSettlement?: {
    state: "pending" | "settled" | "unknown";
    outcome: "ready" | "unchanged" | "unknown";
    updatedAt: number;
  };
}
export interface OwnerState {
  schema: 1;
  dataScope: string;
  current: PreparedRelease | null;
  previous: PreparedRelease | null;
  catalog: Catalog | null;
  lastCheck: number;
  lastCheckSucceeded: boolean;
  preferences: Preferences;
}
export function token(value: unknown, max = 160): string {
  if (
    typeof value !== "string" ||
    !new RegExp(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,${max - 1}}$`).test(value)
  )
    throw Error("invalid_identifier");
  return value;
}
export function identity(value: ReleaseIdentity): ReleaseIdentity {
  token(value?.id, 100);
  if (
    typeof value.version !== "string" ||
    !/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(value.version) ||
    value.version.length > 80
  )
    throw Error("invalid_version");
  if (
    typeof value.revision !== "string" ||
    typeof value.digest !== "string" ||
    !/^[a-f0-9]{40,64}$/.test(value.revision) ||
    !/^[a-f0-9]{64}$/.test(value.digest)
  )
    throw Error("invalid_release_proof");
  return {
    id: value.id,
    version: value.version,
    revision: value.revision,
    digest: value.digest,
  };
}
export function prepared(value: PreparedRelease): PreparedRelease {
  return { identity: identity(value?.identity), handle: token(value?.handle) };
}
export function same(
  a: ReleaseIdentity | undefined,
  b: ReleaseIdentity | undefined,
): boolean {
  return (
    !!a &&
    !!b &&
    ["id", "version", "revision", "digest"].every(
      (k) => a[k as keyof ReleaseIdentity] === b[k as keyof ReleaseIdentity],
    )
  );
}
export function catalog(value: Catalog): Catalog {
  if (!Array.isArray(value?.releases) || value.releases.length > 100)
    throw Error("catalog_limit");
  const releases = value.releases
    .map(identity)
    .sort((a, b) => a.id.localeCompare(b.id));
  if (new Set(releases.map((row) => row.id)).size !== releases.length)
    throw Error("duplicate_release");
  if (
    value.recommendedId !== null &&
    !releases.some((row) => row.id === value.recommendedId)
  )
    throw Error("unknown_recommendation");
  return { releases, recommendedId: value.recommendedId };
}
export function preferences(value: Preferences): Preferences {
  if (
    typeof value.autoCheck !== "boolean" ||
    typeof value.autoInstall !== "boolean" ||
    (value.autoInstall && !value.autoCheck) ||
    !Number.isSafeInteger(value.intervalMs) ||
    value.intervalMs < 1000 ||
    value.intervalMs > 86400000
  )
    throw Error("invalid_preferences");
  return {
    autoCheck: value.autoCheck,
    autoInstall: value.autoInstall,
    intervalMs: value.intervalMs,
  };
}
