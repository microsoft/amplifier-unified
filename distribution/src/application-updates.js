import {FacadeFence} from './facade-fence.js';
/** Public capability facade for an independently lived distribution supervisor. */
const text={type:'string',minLength:1,maxLength:160};
const define=(description,properties={},required=[])=>({description,schema:{type:'object',properties,required,additionalProperties:false}});
const schemas={
 'updates.application.inspect':define('Read installed distribution state and bounded update receipts.'),
 'updates.application.running':define('Read authenticated current process identity without starting it.'),
 'updates.application.check':define('Check current distribution releases immediately without restarting.'),
 'updates.application.install':define('Prepare a qualified application candidate, then wait for proven idle admission before replacement.',{releaseId:text}),
 'updates.application.rollback':define('Reverify and select the retained previous application; automatic installation is disabled.',{expectedCurrentId:text},['expectedCurrentId']),
 'updates.application.preferences':define('Persist automatic distribution check/install preferences.',{autoCheck:{type:'boolean'},autoInstall:{type:'boolean'},intervalMs:{type:'integer',minimum:1000,maximum:86400000}},['autoCheck','autoInstall','intervalMs']),
 'updates.application.receipt':define('Read one exact application update receipt without replay.',{commandId:text},['commandId']),
 'updates.application.reconcile':define('Passively verify an already running replacement after a lost reply; never restart again.',{commandId:text},['commandId']),
 'updates.application.diagnostics':define('Read bounded sanitized application update diagnostics.'),
};
export function createApplicationUpdateCapabilities({supervisor,authorize,directory,onMayBeIdle,onInvalidate=()=>{}}){
 if(supervisor?.outlivesDistribution!==true||!supervisor.owner||typeof supervisor.subscribe!=='function'||typeof authorize!=='function')throw Error('Application updates require an external supervisor and explicit authorization');
 const intake=directory?new FacadeFence({directory,id:'application-updates',onMayBeIdle}):undefined;
 const owner=supervisor.owner,topic='application-updates',uri='amplifier-capability://application-updates';let revision=0,closed=false;
 const changed=()=>{if(!closed){revision++;onInvalidate(topic,'host');}};
 const unsubscribe=supervisor.subscribe(changed);
 const bounded=value=>{if(Buffer.byteLength(JSON.stringify(value))>512*1024)throw Error('Application update projection exceeds its bounded representation');return value;};
 const inspect=async()=>bounded(await owner.inspect());
 const facade={
  quiescenceParticipant:intake?.participant,
  manifest:{version:1,topics:{[topic]:{version:1,uri,watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(schemas).map(operation=>[operation,{topic,operation,method:'x-amplifier/capabilityAction'}]))},
  quiescenceAccess:Object.fromEntries(['inspect','running','diagnostics','receipt','reconcile'].map(name=>['updates.application.'+name,name==='reconcile'?'reconcile':'read'])),
  actionSchemas:()=>schemas,
  async read(request,context){if(closed)throw Error('Application update facade closed');await authorize(context);const target=new URL(request.uri);target.search='';target.hash='';if(request.topic!==topic||request.scope!=='host'||target.href!==uri)throw Error('Application updates require host scope');return {topic,scope:'host',revision,data:{applicationUpdates:await inspect()}};},
  async action(request,context){
   if(closed)throw Error('Application update facade closed');await authorize(context);
   if(request.version!==1||request.topic!==topic||!['host','ahp-root://'].includes(request.channel)||!Object.hasOwn(schemas,request.operation))throw Error('Unadvertised application update action or scope');
   const definition=schemas[request.operation],args=request.args??{};
   if(!args||typeof args!=='object'||Array.isArray(args)||Buffer.byteLength(JSON.stringify(args))>4096||Object.keys(args).some(key=>!Object.hasOwn(definition.schema.properties,key))||definition.schema.required.some(key=>!Object.hasOwn(args,key)))throw Error('Invalid application update arguments');
   let result;const id=request.commandId;
   switch(request.operation){
    case 'updates.application.inspect':result=await inspect();break;
    case 'updates.application.running':result=await owner.inspectRunning();break;
    case 'updates.application.diagnostics':result=await owner.diagnostics();break;
    case 'updates.application.receipt':result={receipt:await owner.receipt(args.commandId),replayed:false};break;
    case 'updates.application.reconcile':result={receipt:await owner.reconcile(args.commandId),replayed:false};break;
    case 'updates.application.check':result={receipt:await owner.check(id,true)};break;
    case 'updates.application.install':result={receipt:await owner.install(id,args.releaseId??null)};break;
    case 'updates.application.rollback':result={receipt:await owner.rollback(id,args.expectedCurrentId)};break;
    case 'updates.application.preferences':result={receipt:await owner.setPreferences(id,args)};break;
   }
   return {accepted:true,result:bounded(result),updates:[]};
  },
  // The supervisor must survive its child distribution's shutdown.
  async close(){closed=true;unsubscribe?.();await intake?.drain();intake?.close();},
 };
 if(intake){const action=facade.action;facade.action=(request,context)=>intake.run(Boolean(facade.quiescenceAccess[request.operation]),()=>action(request,context));}
 return facade;
}
