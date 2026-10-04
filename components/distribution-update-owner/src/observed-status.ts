import {identity, token, type ReleaseIdentity} from "./types.js";

/** A process-local record of a completed deep check, never reusable authority. */
export interface RuntimeVerificationRecord {
  sequence: number;
  completedAt: number;
  outcome: "verified" | "failed";
}
/** Deliberately not assignable to RunningIdentity. Even a recent successful
 * check is historical information, not proof that the current files are intact. */
export interface RuntimeObservation {
  schema: "distribution-runtime-observation-v1";
  binding: {identity: ReleaseIdentity; instanceId: string; dataScope: string};
  observedAt: number;
  readyObserved: boolean;
  integrity: {
    fresh: false;
    lastVerifiedAt: number | null;
    lastCheck: RuntimeVerificationRecord | null;
  };
}
export interface ObservedHostStatus {
  schema: "distribution-observed-status-v1";
  runtime: RuntimeObservation;
  quiescence: {enabled: boolean | null; intakeClosed: boolean | null};
}
function object(value: unknown, names: string[]): Record<string, any> {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).some(key=>!names.includes(key)) ||
      names.some(key=>!Object.hasOwn(value,key))) throw Error("observed_status_invalid");
  return value as Record<string, any>;
}
function timestamp(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value)<0) throw Error("observed_status_invalid");
  return Number(value);
}
export function runtimeObservation(value: unknown): RuntimeObservation {
  const v=object(value,["schema","binding","observedAt","readyObserved","integrity"]);
  const b=object(v.binding,["identity","instanceId","dataScope"]);
  const i=object(v.integrity,["fresh","lastVerifiedAt","lastCheck"]);
  if (v.schema!=="distribution-runtime-observation-v1" || typeof v.readyObserved!=="boolean" || i.fresh!==false)
    throw Error("observed_status_invalid");
  let lastCheck: RuntimeVerificationRecord | null=null;
  if (i.lastCheck!==null) {
    const c=object(i.lastCheck,["sequence","completedAt","outcome"]);
    if (!Number.isSafeInteger(c.sequence) || c.sequence<1 || !["verified","failed"].includes(c.outcome))
      throw Error("observed_status_invalid");
    lastCheck={sequence:c.sequence,completedAt:timestamp(c.completedAt),outcome:c.outcome};
  }
  return {schema:v.schema,binding:{identity:identity(b.identity),instanceId:token(b.instanceId),dataScope:token(b.dataScope)},
    observedAt:timestamp(v.observedAt),readyObserved:v.readyObserved,
    integrity:{fresh:false,lastVerifiedAt:i.lastVerifiedAt===null?null:timestamp(i.lastVerifiedAt),lastCheck}};
}
export function observedHostStatus(value: unknown): ObservedHostStatus {
  const v=object(value,["schema","runtime","quiescence"]),q=object(v.quiescence,["enabled","intakeClosed"]);
  if (v.schema!=="distribution-observed-status-v1" ||
      [q.enabled,q.intakeClosed].some(x=>x!==null&&typeof x!=="boolean")) throw Error("observed_status_invalid");
  return {schema:v.schema,runtime:runtimeObservation(v.runtime),quiescence:{enabled:q.enabled,intakeClosed:q.intakeClosed}};
}
