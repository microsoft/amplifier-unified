/** Distinct held proof for disposal of an exact reviewed managed allocation. */
export function managedRequest(context:any,request:any){
 const sessions=request?.sessions,limit=request?.limit??101,a=request?.allocation;
 if(context?.purpose!=='managed-files-disposal'||!Number.isInteger(limit)||limit<1||limit>101||!Array.isArray(sessions)||!sessions.length||sessions.length>limit||new Set(sessions).size!==sessions.length||sessions.some((s:any)=>typeof s!=='string'||!s.startsWith('ahp-session:/')||s.length>8192||/[\x00-\x1f]/.test(s)))throw Error('Exact managed-files hold and bounded explicit family required');
 if(!a||Object.keys(a).sort().join(',')!=='allocationHash,allocationId,bytes,entryCount,executionDirectory,treeHash'||typeof a.allocationId!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(a.allocationId)||!['allocationHash','treeHash'].every(k=>typeof a[k]==='string'&&/^[0-9a-f]{64}$/.test(a[k]))||!['entryCount','bytes'].every(k=>Number.isSafeInteger(a[k])&&a[k]>=0)||typeof a.executionDirectory!=='string'||!a.executionDirectory.startsWith('/')||a.executionDirectory==='/'||a.executionDirectory.length>8192||/[\x00-\x1f\\]/.test(a.executionDirectory)||a.executionDirectory.split('/').slice(1).some((p:string)=>!p||p==='.'||p==='..'))throw Error('Exact reviewed managed allocation required');
 return {context:structuredClone(context),sessions:[...sessions],limit,allocation:structuredClone(a)};
}
export function emptyManaged(context:any,request:any,current:any){
 const args=managedRequest(context,request),fence=current?.fence??current;
 if(!fence||['fenceId','commandId','purpose','instanceId','dataScope'].some(k=>fence[k]!==context[k])||(fence.state!==undefined&&fence.state!=='held')||(fence.phase!==undefined&&fence.phase!=='held'))throw Error('Exact live held managed-files fence required');
 return {coverage:'complete',protected:[],omissions:[]};
}
export function mergeManaged(base:any,sessions:string[],reasons:(session:string)=>string[]){
 const rows=new Map<string,Set<string>>(base.protected.map((row:any)=>[row.session,new Set<string>(row.reasons)]));
 for(const session of sessions){const extra=reasons(session);if(extra.length)rows.set(session,new Set([...(rows.get(session)??[]),...extra]));}
 return {...base,protected:[...rows].map(([session,values])=>({session,reasons:[...values].sort()}))};
}
export function managedParticipant(participant:any,read:(args:any)=>any,available:()=>Promise<boolean>|boolean=async()=>true){
 return {...participant,managedFiles:{version:1,preservesCanonical:true},reconcileRelease:async(input:any)=>{if(input.purpose==='managed-files-disposal'&&input.proof?.kind==='admission-refused')throw Error('Managed-files rollback requires the original live lease');return participant.reconcileRelease(input);},acquire:async(context:any)=>{
  if(context.purpose!=='managed-files-disposal')return participant.acquire(context);
  if(!await available())return null;
  const exact=structuredClone(context),lease=await participant.acquire(exact);if(!lease)return null;let live=true,selection:string|undefined;
  return {...lease,inspectManagedFilesReferences:async(request:any)=>{
   if(!live)throw Error('Managed-files lease is no longer live');const args=managedRequest(exact,request),key=JSON.stringify([args.sessions,[args.allocation.allocationId,args.allocation.executionDirectory,args.allocation.allocationHash,args.allocation.treeHash,args.allocation.entryCount,args.allocation.bytes]]);
   if(selection!==undefined&&selection!==key)throw Error('Managed-files review identity changed during held inspection');selection=key;
   const result=await read(args);if(!live)throw Error('Managed-files lease changed during inspection');return result;
  },release:async(outcome:any,proof:any)=>{if(!live&&proof?.kind==='admission-refused')throw Error('Managed-files rollback requires the original live lease');live=false;return lease.release(outcome,proof);}};
 }};
}
