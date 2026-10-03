export type Json=Record<string,any>;
export interface HistoryOptions {
 directory:string; engineId:string;
 authorizeWorkspace:(directory:string,context:Json)=>Promise<string>;
 nativeAdmin:(operation:string,args:Json,context:Json & {workingDirectory:string})=>Promise<Json|null>;
 adoptImportedSession:(input:{commandId:string;session:string;engineId:string;workingDirectory:string;importCommandId:string;sourceSha256:string;previewHash:string})=>Promise<Json>;
 importAdoptionReceipt:(commandId:string,options?:{reconcile?:boolean})=>Promise<Json|null>;
 onInvalidate?:(topic:string,scope:string)=>unknown; onMayBeIdle?:()=>unknown;
}
export declare const importActions:Record<string,{description:string;schema:Json}>;
export declare const importLimits:Readonly<{maxBytes:number;chunkBytes:number;concurrentRequests:number;receiptBytes:number}>;
export declare function createHistoryCapability(options:HistoryOptions):{
 manifest:Json; actionSchemas:()=>Promise<typeof importActions>;quiescenceAccess:Record<string,'read'>;
 quiescenceParticipant:{id:string;acquire:(context:Json)=>Promise<null|{ownerId:string;fenceId:string;release:(outcome:string,proof:Json)=>Promise<void>}>;reconcileRelease:(input:Json)=>Promise<void>};
 inspectQuiescence:()=>Promise<Json>;read:(request:Json)=>Promise<Json>;action:(request:Json,context?:Json)=>Promise<Json>;close:()=>Promise<void>;
};
