import {identity, same, token, type RunningIdentity} from './types.js';

/** A failed acquisition is not a held zero-work admission. Keep its recovery
 * authority distinct; never manufacture AdmissionEvidence from this binding. */
export interface AdmissionAbortBinding {
  commandId: string;
  fenceId: string;
  purpose: 'distribution-update';
  instanceId: string;
  dataScope: string;
}
export interface AdmissionAbortRequest extends AdmissionAbortBinding {
  observed: RunningIdentity;
}
export interface AdmissionAbortOwnerReceipt {
  ownerId: string;
  fenceId: string;
  commandId: string;
  instanceId: string;
  dataScope: string;
  status: 'released' | 'not-acquired';
  receiptId: string;
}
/** Host asserts this is the COMPLETE attempted-owner census, after serialized
 * reverse unwind. Missing/uncertain evidence must not produce this receipt. */
export interface HostAdmissionAbortReceipt {
  schema: 'host-admission-abort-v1';
  commandId: string;
  fenceId: string;
  instanceId: string;
  dataScope: string;
  status: 'aborted';
  intakeClosed: false;
  owners: AdmissionAbortOwnerReceipt[];
  receiptId: string;
}
export interface AdmissionAbortIntent {
  request: AdmissionAbortRequest;
  requestedAt: number;
  receipt?: HostAdmissionAbortReceipt;
  settledAt?: number;
}
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('admission_abort_unconfirmed');
  return value as Record<string, unknown>;
};
const exact = (value: Record<string, unknown>, fields: string[]) => {
  if (Object.keys(value).sort().join(',') !== [...fields].sort().join(',')) throw Error('admission_abort_unconfirmed');
};
const bindings = ['commandId','fenceId','instanceId','dataScope'] as const;
export function admissionAbortBinding(value: unknown): AdmissionAbortBinding {
  const v = object(value);
  exact(v, [...bindings, 'purpose']);
  if (v.purpose !== 'distribution-update') throw Error('admission_abort_unconfirmed');
  return {commandId:token(v.commandId),fenceId:token(v.fenceId),instanceId:token(v.instanceId),dataScope:token(v.dataScope),purpose:v.purpose};
}
export function admissionAbortRequest(value: unknown): AdmissionAbortRequest {
  const v = object(value);
  exact(v, [...bindings, 'purpose','observed']);
  const {observed, ...rest} = v, binding = admissionAbortBinding(rest), r = object(observed);
  exact(r, ['identity','instanceId','dataScope','ready']);
  if (r.ready !== true || r.instanceId !== binding.instanceId || r.dataScope !== binding.dataScope) throw Error('admission_abort_unconfirmed');
  return {...binding, observed:{identity:identity(r.identity as RunningIdentity['identity']),instanceId:binding.instanceId,dataScope:binding.dataScope,ready:true}};
}
export function sameAdmissionAbort(a: AdmissionAbortRequest, b: AdmissionAbortRequest): boolean {
  return bindings.every(k=>a[k]===b[k]) && a.purpose===b.purpose && same(a.observed.identity,b.observed.identity);
}
export function admissionAbortReceipt(value: unknown, expected: AdmissionAbortBinding): HostAdmissionAbortReceipt {
  const v=object(value);
  exact(v, [...bindings,'schema','status','intakeClosed','owners','receiptId']);
  if(v.schema!=='host-admission-abort-v1' || v.status!=='aborted' || v.intakeClosed!==false ||
    !bindings.every(k=>v[k]===expected[k]) || !Array.isArray(v.owners) || v.owners.length>128)
    throw Error('admission_abort_unconfirmed');
  const owners=v.owners.map(value=>{
    const row=object(value);exact(row,[...bindings,'ownerId','status','receiptId']);
    if(!bindings.every(k=>row[k]===expected[k]) || !['released','not-acquired'].includes(String(row.status))) throw Error('admission_abort_unconfirmed');
    return {ownerId:token(row.ownerId),commandId:expected.commandId,fenceId:expected.fenceId,instanceId:expected.instanceId,
      dataScope:expected.dataScope,status:row.status as AdmissionAbortOwnerReceipt['status'],receiptId:token(row.receiptId)};
  });
  if(new Set(owners.map(row=>row.ownerId)).size!==owners.length) throw Error('admission_abort_unconfirmed');
  return {schema:'host-admission-abort-v1',commandId:expected.commandId,fenceId:expected.fenceId,instanceId:expected.instanceId,
    dataScope:expected.dataScope,status:'aborted',intakeClosed:false,owners,receiptId:token(v.receiptId)};
}
