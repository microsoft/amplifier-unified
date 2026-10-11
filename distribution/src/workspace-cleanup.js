import {createHash} from 'node:crypto';
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const receipt=(value,commandId,operation)=>{
 const r=value?.receipt;
 if(r?.commandId!==commandId||r.operation!==operation||r.state!=='succeeded'||r.result?.archived!==true||r.result.historyPreserved!==true||r.result.replayed!==false)throw Error('Exact completed workspace archival receipt required');
 return r;
};

/** Composition only. Native filesystem authority remains inside recovery. */
export async function workspaceCleanupPort({workspaces,catalog,host,engineId}){
 if(!await workspaces?.supportsWorkspaceCleanup?.()||(await catalog?.libraryCapabilities?.())?.workspaceCleanupCensus?.version!==1)return;
 const combined=async commandId=>{
  const w=receipt(await workspaces.workspaceCleanupReceipt(commandId+':workspace'),commandId+':workspace','workspace.cleanup.archiveRegistration');
  const h=receipt(await host().workspaceCleanupReceipt(commandId+':chats'),commandId+':chats','workspace.cleanup.archiveChats');
  if(w.result.workspaceId!==h.result.workspaceId||w.result.nativeCommandId!==h.result.nativeCommandId)throw Error('Workspace and chat archive receipts disagree');
  return {receipt:{commandId,operation:'workspace.cleanup.archive',state:'succeeded',result:{workspaceId:w.result.workspaceId,archived:true,historyPreserved:true,nativeCommandId:w.result.nativeCommandId,replayed:false}}};
 };
 return {
  version:1,
  async inspect(workspaceId,fence){
   const registration=await workspaces.inspectWorkspaceCleanup(workspaceId,fence);
   if(registration?.coverage!=='workspace-registrations-and-external-resources'||registration.workspaceId!==workspaceId)throw Error('Complete workspace registration evidence required');
   const identities=await host().inspectWorkspaceCleanup({path:registration.path,engineId},fence);
   if(identities?.version!==1||identities.coverage!=='indexed-and-host-identities')throw Error('Complete Host identity coverage required');
   const product={registration,archiveSessions:identities.archiveSessions};
   return {workspaceId,path:registration.path,revision:hash([registration,identities]),coverage:'all-configured-owners-held',registeredPaths:registration.registeredPaths,protectedPaths:identities.protectedPaths,sessions:identities.sessions,product};
  },
  async archive({commandId,selection,nativeReceipt},fence){
   const product=selection.product;
   if(!product?.registration||product.registration.workspaceId!==selection.workspaceId||product.registration.path!==selection.path||!Array.isArray(product.archiveSessions))throw Error('Retained original product cleanup selection required');
   // Each owner deduplicates this exact metadata command. Native removal is
   // never reachable from this callback, including explicit settlement.
   await host().archiveWorkspaceCleanup({commandId:commandId+':chats',workspaceId:selection.workspaceId,path:selection.path,sessions:product.archiveSessions,nativeReceipt},fence);
   await workspaces.archiveWorkspaceCleanup({commandId:commandId+':workspace',registration:product.registration,nativeReceipt},fence);
   return combined(commandId);
  },
  receipt:combined,
 };
}
