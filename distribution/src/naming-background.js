/** Bounded discovery scheduling. Native owns provider choice, attempts and metadata. */
export function createBackgroundNaming({host,nativeNaming,list,idle=()=>true,intervalMs=15000}){
 let timer,running,closed=false,cursor,remaining=[],failures=0,named=0,inspected=0;
 const ready=()=>!closed&&host().inspectBackgroundReadiness?.().ready===true&&idle();
 async function scan(){
  if(!ready())return;
  if(!remaining.length){const page=await list(cursor);if(!Array.isArray(page?.items)||page.items.length>32)throw Error('Bounded naming discovery page required');remaining=[...page.items];cursor=page.nextCursor||undefined;}
  let attempts=0;
  while(remaining.length){
   if(!ready())break;
   const row=remaining.shift();
   if(row.parentUri||!row.nativeSessionId||row.availability==='missing'||row.workspaceAvailability==='missing'||row.isArchived||row.productHidden||row.nativeDeleted)continue;
   inspected++;
   // Readiness is advisory and checked again at each admission. This public
   // Host boundary and the admin participant cover the entire cold request.
   let result;try{result=await host().withExternalMutation('composition',()=>nativeNaming(row.uri,'backfill',{}));}catch{failures++;if(++attempts>=2)break;continue;}
   if(result.applied&&result.title){
    await host().commitSessionTitle(row.uri,{commandId:result.commandId},async()=>({applied:true,title:result.title}));
    named++;
   }
   if(!['ineligible','empty','provider-unavailable','changed-before-admission'].includes(result.status)&&++attempts>=2)break;
  }
 }
 const tick=()=>{if(closed||running)return running??Promise.resolve();running=scan().catch(()=>{cursor=undefined;remaining=[];failures++;}).finally(()=>{running=undefined;});return running;};
 return {tick,start(){if(!closed&&!timer){timer=setInterval(()=>void tick(),intervalMs);timer.unref?.();}},diagnostics:()=>({pending:running?1:0,failures,dropped:0,named,inspected}),async close(){closed=true;clearInterval(timer);await running;}};
}
