import {createHash, randomUUID} from "node:crypto";
import {Store} from "./store.js";
import {preferences, token, type Preferences} from "./types.js";

type Json = Record<string, any>;
export interface PreferencesRecoveryFence {
  fenceId: string;
  commandId: string;
  purpose: "recovery";
  instanceId: string;
  dataScope: string;
}
export interface PreferencesResetPort {
  id: "updates";
  parts: string[];
  perform(operation: string, args: Json, fence?: unknown, context?: unknown): Promise<Json>;
}
export interface PreferencesResetReceipt {
  ownerId: "updates";
  commandId: string;
  operation: string;
  state: "succeeded" | "refused";
  executed: boolean;
  replayed: false;
  createdAt: number;
  settledAt: number;
  result?: Json;
}
const parts = ["updates.preferences"];
const preserved = ["releases and rollback history", "source configuration and trust", "update command receipts", "shared settings and credentials", "conversation history"];
const disabled: Preferences = {autoCheck:false, autoInstall:false, intervalMs:14400000};
const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(k => [k,item[k]])) : item);
const digest = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const refusal = () => Object.assign(Error("preferences_reset_refused"), {data:{executed:false}});
function publicReceipt(receipt: Json): Json {
  const value=Object.fromEntries(["ownerId","commandId","operation","state","executed","replayed","createdAt","settledAt"].filter(k=>Object.hasOwn(receipt,k)).map(k=>[k,receipt[k]]));
  // Failed/running/unknown records may retain private diagnostic data. Never
  // export that data as a result, including during passive receipt recovery.
  if(receipt.state==="succeeded")value.result=structuredClone(receipt.result);
  return value;
}
function keys(value: Json, allowed: string[], required = allowed) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(k => !allowed.includes(k)) || required.some(k => !Object.hasOwn(value,k))) throw refusal();
}
export function preferencesRecoveryFence(value: unknown): PreferencesRecoveryFence {
  const v=value as Json;
  keys(v,["fenceId","commandId","purpose","instanceId","dataScope"]);
  if(v.purpose!=="recovery") throw refusal();
  try {return {fenceId:token(v.fenceId),commandId:token(v.commandId),purpose:"recovery",instanceId:token(v.instanceId),dataScope:token(v.dataScope)};}
  catch {throw refusal();}
}

/** Private before-images and exact original receipts stay in the supervisor's
 * existing SQLite authority. Public reviews never contain preference values.
 * The caller must hold the full host recovery lease until this call settles;
 * the trusted verifier checks that live fence, not caller-supplied assertions. */
export class PreferencesReset implements PreferencesResetPort {
  readonly id = "updates" as const;
  readonly parts = [...parts];
  constructor(private options: {
    store: Store;
    dataScope: string;
    verify(fence: PreferencesRecoveryFence): Promise<void>;
    exclusive<T>(work: () => Promise<T>): Promise<T>;
    onChange?(receipt: PreferencesResetReceipt): void;
  }) {}
  private args(operation: string, args: Json) {
    if (operation === "prepare") {
      keys(args,["commandId","parts","privateContentReviewed","restoreCommandId"],["commandId","parts","privateContentReviewed"]);
      if(canonical(args.parts)!==canonical(parts)||args.privateContentReviewed!==true) throw refusal();
      if(args.restoreCommandId!==undefined) token(args.restoreCommandId);
    } else if(operation === "apply" || operation === "restore") {
      keys(args,["commandId","preparedId","reviewHash",...(operation==="restore"?["resetCommandId","expectedPostResetRevision"]:[])]);
      token(args.preparedId);
      if(!/^[a-f0-9]{64}$/.test(args.reviewHash)) throw refusal();
      if(operation==="restore"){token(args.resetCommandId);token(args.expectedPostResetRevision);}
    } else throw refusal();
    token(args.commandId);
  }
  private applicable(row: Json | null, reviewHash: unknown) {
    return !!row && !row.usedBy && row.review.reviewHash===reviewHash && row.review.expiresAt>Date.now() && row.review.revision===this.options.store.preferenceRevision();
  }
  async perform(operation: string, input: Json, context?: unknown, _actor?: unknown): Promise<Json> {
    // Snapshot caller objects before awaits: mutation of an in-process DTO must
    // not change the arguments whose command identity was checked.
    const args=structuredClone(input), store=this.options.store;
    if(operation==="inspect"){
      if(Object.hasOwn(args,"commandId")){
        keys(args,["commandId"]);
        const found=store.resetCommand(token(args.commandId));
        return {receipt:found?publicReceipt(found.receipt):null};
      }
      keys(args,["preparedId","reviewHash"]);
      return this.options.exclusive(async()=>{
        const fence=preferencesRecoveryFence(context);
        if(fence.dataScope!==this.options.dataScope) throw refusal();
        await this.options.verify(fence);
        const row=store.resetReview(token(args.preparedId));
        return {applicable:this.applicable(row,args.reviewHash),revision:store.preferenceRevision()};
      });
    }
    this.args(operation,args);
    const fingerprint=digest([operation,args]), old=store.resetCommand(args.commandId);
    if(old){if(old.fingerprint!==fingerprint)throw refusal();return {receipt:publicReceipt(old.receipt)};}
    const now=Date.now();
    let committed=false;
    try {
      const result=await this.options.exclusive(async()=>{
        const fence=preferencesRecoveryFence(context);
        if(fence.dataScope!==this.options.dataScope) throw refusal();
        await this.options.verify(fence);
        return store.transaction(()=>{
          const repeated=store.resetCommand(args.commandId);
          if(repeated){if(repeated.fingerprint!==fingerprint)throw refusal();return repeated.receipt;}
          let result: Json;
          if(operation==="prepare"){
            const revision=store.preferenceRevision(), before=preferences(store.state().preferences);
            let after=disabled;
            if(args.restoreCommandId){
              const original=store.resetCommand(args.restoreCommandId)?.receipt;
              if(original?.state!=="succeeded"||original.operation!=="apply"||original.result?.postResetRevision!==revision)throw refusal();
              const originalReview=store.resetReview(original.result.preparedId);
              if(originalReview?.usedBy!==args.restoreCommandId)throw refusal();
              after=preferences(originalReview!.before);
            }
            const publicFields={ownerId:this.id,preparedId:randomUUID(),parts:[...parts],revision,expiresAt:Date.now()+600000,containsPrivateContent:true,credentialsIncluded:false,coverage:"explicit-app-local-parts",preserved:[...preserved],omissions:["all other application settings"],restoresCommandId:args.restoreCommandId??null,items:[{part:parts[0],present:true,operation:args.restoreCommandId?"restore":"reset-to-disabled-defaults"}]};
            result={...publicFields,reviewHash:digest(publicFields)};
            store.saveResetReview(result.preparedId,{review:result,before,after});
          }else{
            const row=store.resetReview(args.preparedId);
            if(!this.applicable(row,args.reviewHash))throw refusal();
            if(operation==="restore"){
              const original=store.resetCommand(args.resetCommandId)?.receipt;
              if(row!.review.restoresCommandId!==args.resetCommandId||original?.state!=="succeeded"||original.operation!=="apply"||original.result.postResetRevision!==args.expectedPostResetRevision||store.preferenceRevision()!==args.expectedPostResetRevision)throw refusal();
            }else if(row!.review.restoresCommandId!==null)throw refusal();
            const state=store.state();state.preferences=preferences(row!.after);
            store.save(state,true);
            row!.usedBy=args.commandId;
            store.saveResetReview(args.preparedId,row!);
            result={ownerId:this.id,preparedId:args.preparedId,reviewHash:args.reviewHash,parts:[...parts],postResetRevision:store.preferenceRevision(),restored:operation==="restore",preserved:[...preserved],replayed:false,canonicalFilesChanged:0,sharedSettingsChanged:false};
          }
          const receipt: PreferencesResetReceipt={ownerId:this.id,commandId:args.commandId,operation,state:"succeeded",executed:true,replayed:false,createdAt:now,settledAt:Date.now(),result};
          store.saveResetCommand(args.commandId,fingerprint,receipt);
          return receipt;
        });
      });
      committed=true;
      try {this.options.onChange?.(result as PreferencesResetReceipt);} catch {/* Observation cannot change committed truth. */}
      return {receipt:publicReceipt(result)};
    }catch(error){
      // No result is projected for a refusal/unknown. An uncertain storage error
      // escapes so the caller retains its recovery hold and inspects the command.
      if(committed || !(error as any)?.data || (error as any).data.executed!==false)throw error;
      const receipt:PreferencesResetReceipt={ownerId:this.id,commandId:args.commandId,operation,state:"refused",executed:false,replayed:false,createdAt:now,settledAt:Date.now()};
      const retained=store.resetCommand(args.commandId);
      if(retained){if(retained.fingerprint!==fingerprint)throw refusal();return {receipt:publicReceipt(retained.receipt)};}
      store.saveResetCommand(args.commandId,fingerprint,receipt);
      return {receipt};
    }
  }
}
