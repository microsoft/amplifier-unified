import {z} from 'zod';
import {FacadeFence} from './facade-fence.js';

const text=z.string().min(1).max(200),session=z.string().regex(/^ahp-session:\/[^\s]{1,180}$/);
const unique=(schema,max)=>z.array(schema).max(max).refine(items=>new Set(items).size===items.length,'Duplicate identities');
const definitions={
 'cleanup.preview':{description:'Review one bounded metadata page of old conversations. Native history is preserved; this does not authorize removal.',input:z.object({modifiedBefore:z.number().int().nonnegative(),limit:z.number().int().min(1).max(50).optional(),cursor:z.string().min(1).max(4096).optional(),workingDirectory:z.string().min(1).max(8192).optional(),protectSessionIds:unique(session,200).optional()}).strict()},
 'cleanup.apply':{description:'Hide exactly the reviewed eligible conversations after checking current activity and held owner protection. All canonical history and product records remain.',input:z.object({reviewId:text,reviewHash:z.string().regex(/^[a-f0-9]{64}$/),sessionIds:unique(session,50).min(1)}).strict()},
 'cleanup.receipt':{description:'Read the original cleanup outcome after a lost response. Never repeats cleanup.',input:z.object({commandId:text}).strict()},
 'cleanup.reconcile':{description:'Reconcile held product protection using the exact settled native cleanup receipt. Never repeats a native effect.',input:z.object({commandId:text}).strict()},
};
const topic='history-cleanup',uri='amplifier-capability://history-cleanup';
const description={mode:'preserve-history-hide',pageLimit:50,canonicalHistoryPreserved:true,productRecordsPreserved:true,legacyFilesPurged:false,previewAuthority:'metadata-only',dependencyCheck:'at-apply'};
const bounded=value=>{if(Buffer.byteLength(JSON.stringify(value))>512*1024)throw Error('Cleanup projection exceeds its bound');return value;};

/** A thin shared user/agent facade. The host owns review identities and durable
 * command outcomes; this facade stores only its own forwarding intake fence. */
export function createHistoryCleanupCapabilities({host,protection,authorize,directory,onMayBeIdle,onInvalidate=()=>{}}){
 if(typeof host!=='function'||typeof authorize!=='function'||!directory)throw Error('Cleanup requires trusted host, authorization and owned intake storage');
 const gate=new FacadeFence({directory,id:'history-cleanup',onMayBeIdle,serviceStop:true});let revision=0,closed=false;
 const schemas=Object.fromEntries(Object.entries(definitions).map(([name,{description,input}])=>[name,{description,schema:z.toJSONSchema(input)}]));
 const check=async context=>{if(closed)throw Error('Cleanup facade closed');await authorize(context);};
 return {
  manifest:{version:1,topics:{[topic]:{version:1,uri,watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(definitions).map(operation=>[operation,{topic,operation,method:'x-amplifier/capabilityAction'}]))},
  quiescenceParticipant:gate.participant,quiescenceAccess:{'cleanup.receipt':'read'},actionSchemas:()=>schemas,
  async read(request,context){await check(context);if(request.topic!==topic||request.scope!=='host'||request.uri!==uri)throw Error('Cleanup requires host scope');return {topic,scope:'host',revision,data:{historyCleanup:description}};},
  async action(request,context){
   await check(context);
   if(request.version!==1||request.topic!==topic||!['ahp-root://','host'].includes(request.channel)||!Object.hasOwn(definitions,request.operation))throw Error('Unadvertised cleanup operation or scope');
   const args=definitions[request.operation].input.parse(request.args??{});
   return gate.run(request.operation==='cleanup.receipt',async()=>{
    let result;
    if(request.operation==='cleanup.receipt')result={receipt:await host().retentionReceipt(args.commandId),replayed:false};
    else if(request.operation==='cleanup.reconcile'){
     if(typeof protection!=='function'||!protection())throw Error('Cleanup protection authority is not configured');
     result={protection:await protection().reconcile(args.commandId),replayed:false};
    }
    else if(request.operation==='cleanup.preview'){
     // Identity belongs to the authenticated transport, never supplied in args.
     const connectionId=text.parse(context.clientId||context.actorId);
     result=await host().previewRetention({...args,connectionId});
    }else{
     result=await host().applyRetention({...args,commandId:text.parse(request.commandId)});
     revision++;onInvalidate(topic,'host');
    }
    return {accepted:true,result:bounded(result??null),updates:[]};
   });
  },
  async close(){closed=true;await gate.drain();gate.close();},
 };
}
