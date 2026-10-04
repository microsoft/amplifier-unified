import type {ConversationPresentationPort,PresentationJob} from './presentation-types.js';
import type {ServiceIdentity,ServiceReleaseFields} from './service-lifecycle.js';
export type Json = Record<string, any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Identity {accountId:string;}
export interface NativeSession {nativeSessionId:string;historyCwd:string;nativeAuthority:string;}
export interface FenceContext {fenceId:string;commandId:string;purpose:'recovery'|'distribution-update'|'service-stop'|'retention-hide'|'managed-files-disposal';instanceId:string;dataScope:string;serviceIdentity?:ServiceIdentity;}
export type ReleaseProof={verified:true;fenceId:string;commandId:string;outcome:'unchanged'|'ready';instanceId:string;dataScope:string;receiptId:string}&Partial<ServiceReleaseFields>;
export interface QuiescencePort {
 admitQuiescence(input:{commandId:string;purpose:'recovery'}):Promise<Json>;
 inspectQuiescence():Json|Promise<Json>;
 quiescenceReceipt(commandId:string):Json|Promise<Json|undefined>|undefined;
 releaseQuiescence(input:{fenceId:string;commandId:string;outcome:'unchanged'|'unknown';evidence:Json}):Promise<Json>;
 withQuiescenceMaintenance<T>(input:{fenceId:string;commandId:string},callback:()=>Promise<T>):Promise<T>;
}
export interface AppResetOwner {id:string;parts:string[];perform:(operation:string,args:Json,fence:FenceContext|undefined,context:Context)=>Promise<Json>;}
export interface Options {
 /** Separately negotiated host-owned visibility reset; never native maintenance. */
 conversationPresentation?:ConversationPresentationPort;
 /** Trusted registered owner ports; private bodies stay inside each owner. */
 appResetOwners?:AppResetOwner[];
 /** Private product-owner database only; never the native home or another owner's directory. */
 directory:string;
 /** One configured private ACP admin authority/connection; do not silently reconnect an active lease. */
 nativeAuthority:string;
 /** Exact initialized native.admin.maintenance metadata from this authority. Absent keeps legacy selected-only behavior. */
 nativeMaintenance?:Json;
 /** Labels and opaque launcher root IDs only, never filesystem paths. */
 restoreDestinationChoices?:{id:string;label:string}[];
 onMayBeIdle?:()=>void;
 nativeAdmin:(operation:string,args:Json,context:Context)=>Promise<Json>;
 /** Authenticates every read and explicitly approves sensitive mutations; client args are not authority. */
 authorize:(context:Context,operation:string,args:Readonly<Json>)=>Promise<Identity>;
 resolveSession:(session:string,context:Context)=>Promise<NativeSession>;
 quiescence:QuiescencePort;
 onInvalidate?:(topic:'recovery',scope:'host')=>void;
 leaseSeconds?:number;
}
/** Generic ACP hosts need only host-owned visibility, not Amplifier native admin. */
export interface PresentationOptions {
 directory:string;
 conversationPresentation:ConversationPresentationPort;
 authorize:(context:Context,operation:string,args:Readonly<Json>)=>Promise<Identity>;
 onInvalidate?:(topic:'recovery',scope:'host')=>void;
 onMayBeIdle?:()=>void;
}
export type JobState='queued'|'quiescing'|'running'|'releasing'|'prepared'|'succeeded'|'refused'|'unknown';
export interface Job {
 id:string;accountId:string;commandId:string;operation:string;args:Json;signature:string;
 state:JobState;createdAt:number;updatedAt:number;revision:number;context:Context;
 sessions:{session:string;nativeSessionId:string;historyCwd:string;nativeAuthority:string}[];
 fenceCommandId:string;fence?:FenceContext;preview?:Json;preparedJobId?:string;snapshotJobId?:string;
 presentation?:PresentationJob;
 appResetCommands?:Record<string,string>;appResetReceipts?:Record<string,Json>;
 nativeOperation?:string;nativeCommandId?:string;nativeResult?:Json;leaseId?:string;nativeLeaseReleased?:boolean;
 result?:Json;reason?:string;releaseEvidence?:Json;terminalState?:'prepared'|'succeeded'|'refused';
}
