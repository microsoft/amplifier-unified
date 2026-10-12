/** Trusted composition. Native owns bytes; Host owns held references and visibility. */
export async function sharedHistoryCleanupPort({catalog,host,engineId,canonicalOwner}){
 if(typeof canonicalOwner!=='string'||!canonicalOwner||(await catalog?.libraryCapabilities?.())?.workspaceCleanupCensus?.version!==1)return;
 return {
  version:1,
  inspect:(census,fence)=>host().inspectHistoryCleanup({census,engineId,canonicalOwners:[canonicalOwner]},fence),
  settle:(args,fence)=>host().settleHistoryCleanup(args,fence),
  receipt:commandId=>host().historyCleanupReceipt(commandId),
 };
}
