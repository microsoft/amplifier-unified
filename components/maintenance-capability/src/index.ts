/** Native runtime maintenance; application distribution update is a different owner. */
export type Json=Record<string,any>;
export interface Context {clientId:string;origin?:'ui'|'agent';session?:string|{uri:string};}
export interface Options {
 nativeAdmin:(operation:string,args:Json,context:Context)=>Promise<Json>;
 /** Trusted authorization, never inferred from args.cwd or client-provided paths. */
 authorize:(context:Context)=>Promise<void>;
 inspectRuntimeCurrency?:()=>Promise<Json>;
 /** Inspects only an already resident worker; must never mount or resume. */
 inspectResidentRuntime?:(session:string,args:Json,context:Context)=>Promise<Json>;
 onInvalidate?:(topic:string,scope:string)=>void;
}
const definitions:Record<string,{description:string;schema:Json}>={
 'updates.check':{description:'Check configured native runtime sources without changing installed workers.',schema:{type:'object',properties:{},additionalProperties:false}},
 'updates.install':{description:'Prepare, verify and select a native runtime generation for future workers.',schema:{type:'object',properties:{checkId:{type:'string'}},additionalProperties:false}},
 'updates.rollback':{description:'Select the previous qualified native generation for future workers.',schema:{type:'object',properties:{expectedCurrent:{type:['string','null']}},required:['expectedCurrent'],additionalProperties:false}},
 'updates.runtime.inspect':{description:'Read a bounded native source inventory and runtime currency report.',schema:{type:'object',properties:{cursor:{type:'string',maxLength:200},limit:{type:'integer',minimum:1,maximum:100}},additionalProperties:false}},
 'updates.runtime.receipt':{description:'Inspect one exact native maintenance receipt after a lost response. Never repeats work.',schema:{type:'object',properties:{commandId:{type:'string',minLength:1,maxLength:200}},required:['commandId'],additionalProperties:false}},
 'updates.runtime.current':{description:'Read a bounded qualified installation receipt and configured source policy; this does not establish live worker adoption.',schema:{type:'object',properties:{generation:{type:'string',maxLength:200},cursor:{type:'string',maxLength:200},limit:{type:'integer',minimum:1,maximum:100},expectedRevision:{type:'string',maxLength:200}},additionalProperties:false}},
 'updates.runtime.worker':{description:'Inspect bounded source evidence from an already resident worker without starting one.',schema:{type:'object',properties:{sessionId:{type:'string',maxLength:300},surface:{enum:['distributions','mounted']},refresh:{type:'boolean'},cursor:{type:'string',maxLength:200},limit:{type:'integer',minimum:1,maximum:100},expectedRevision:{type:'string',maxLength:200}},required:['sessionId'],additionalProperties:false}},
 'updates.runtime.repair.preview':{description:'Review exact retained native generation receipts for reconstruction into a new environment.',schema:{type:'object',properties:{generation:{type:'string',maxLength:200}},additionalProperties:false}},
 'updates.runtime.repair':{description:'Reconstruct and qualify a new environment from reviewed retained receipts. Does not select it or change running workers.',schema:{type:'object',properties:{generation:{type:'string',maxLength:200},expectedSourceHash:{type:'string',pattern:'^[a-f0-9]{64}$'},expectedCurrent:{type:['string','null']}},required:['generation','expectedSourceHash','expectedCurrent'],additionalProperties:false}},
 'updates.runtime.select':{description:'Separately select a qualified generation for future workers using the reviewed current pointer.',schema:{type:'object',properties:{generation:{type:'string',maxLength:200},expectedCurrent:{type:['string','null']}},required:['generation','expectedCurrent'],additionalProperties:false}},
};
// This bridge proof is emitted before dispatch when the initialized peer lacks
// the launcher grant. It is not a general interpretation of executed:false.
function generationsUnavailable(error:unknown):boolean {
 const data=(error as {data?:unknown})?.data;
 return !!data&&typeof data==='object'&&!Array.isArray(data)
  &&Object.keys(data).sort().join(',')==='executed,reason,replayed'
  &&(data as Json).reason==='native-generations-unavailable'
  &&(data as Json).executed===false&&(data as Json).replayed===false;
}
export class MaintenanceCapabilities {
 readonly manifest={version:1,topics:{maintenance:{version:1,uri:'amplifier-capability://maintenance/maintenance',watch:true,scope:'host'}},actions:Object.fromEntries(Object.keys(definitions).map(operation=>[operation,{topic:'maintenance',operation,method:'x-amplifier/capabilityAction'}]))};
 private revision=0;private cached?:Json;private closed=false;
 constructor(private options:Options){}
 actionSchemas(){return definitions;}
 private async inspect(args:Json,context:Context):Promise<Json>{
  const result=await this.options.nativeAdmin('generations.inspect',args,context);
  const currency=this.options.inspectRuntimeCurrency?await this.options.inspectRuntimeCurrency():{status:'unavailable',detail:'Running worker currency is not supplied by this host.'};
  if(Buffer.byteLength(JSON.stringify(currency))>65536)throw Error('Runtime currency report exceeds its bounded representation');
  return {...result,runtimeCurrency:currency};
 }
 private data(result:Json){return {updates:{...result,application:{status:'not_managed',current:'',detail:'This owner manages native agent runtimes. Application updates belong to the installed distribution.'}},maintenance:{nativeRuntime:true,applicationUpdateSupported:false}};}
 async read(params:Json,context:Context={clientId:params.clientId}){
  if(this.closed)throw Error('Maintenance owner closed');await this.options.authorize(context);
  const identity=new URL(params.uri);identity.search='';identity.hash='';
  if(identity.href!==this.manifest.topics.maintenance.uri||params.topic!=='maintenance'||params.scope!=='host')throw Error('Maintenance is an explicitly authorized host scope');
  if(!this.cached)this.cached=this.data(await this.inspect({limit:50},context));
  return {topic:'maintenance',scope:'host',revision:this.revision,data:this.cached};
 }
 async action(params:Json,context:Context){
  if(this.closed)throw Error('Maintenance owner closed');await this.options.authorize(context);
  if(params.version!==1||params.topic!=='maintenance'||!['host','ahp-root://'].includes(params.channel)||!(params.operation in definitions))throw Error('Unknown maintenance action or scope');
  const operation=params.operation,args=params.args??{},commandId=params.commandId;
  if(typeof args!=='object'||!args||Array.isArray(args)||Buffer.byteLength(JSON.stringify(args))>32768)throw Error('Bounded maintenance args required');
 if(Object.keys(args).some(key=>!(key in definitions[operation].schema.properties)))throw Error('Unexpected maintenance argument');
  for(const key of definitions[operation].schema.required??[])if(!(key in args))throw Error('Required maintenance argument: '+key);
  let result:Json;
  if(operation==='updates.runtime.inspect')return {accepted:true,result:await this.inspect(args,context),updates:[]};
  if(operation==='updates.runtime.current'||operation==='updates.runtime.repair.preview')return {accepted:true,result:await this.options.nativeAdmin(operation==='updates.runtime.current'?'generations.current':'generations.repair.preview',args,context),updates:[]};
  if(operation==='updates.runtime.worker'){
   const {sessionId,...page}=args;
   if(typeof sessionId!=='string'||!/^ahp-session:\/[^/?#]+$/.test(sessionId))throw Error('An exact host session is required');
   const own=typeof context.session==='string'?context.session:context.session?.uri;
   if(context.origin==='agent'&&own!==sessionId)throw Error('Agent runtime inspection cannot select another conversation');
   if(!this.options.inspectResidentRuntime)return {accepted:true,result:{available:false,workerStarted:false,reason:'Resident worker inspection is not supplied by this host.'},updates:[]};
   return {accepted:true,result:await this.options.inspectResidentRuntime(sessionId,page,context),updates:[]};
  }
  if(operation==='updates.runtime.receipt'){
   if(typeof args.commandId!=='string'||!args.commandId)throw Error('Exact receipt commandId required');
   const base=args.commandId;
   result={commandId:base,receipts:await Promise.all([base,base+':prepare',base+':promote'].map(commandId=>this.options.nativeAdmin('generations.receipt',{commandId},context).then(row=>row.receipt).catch(error=>{throw error;})))};
   return {accepted:true,result,updates:[]};
  }
  if(typeof commandId!=='string'||!commandId||commandId.length>180)throw Error('Durable bounded commandId required');
  let preparationReturned=false;
  try {
  if(operation==='updates.check')result=await this.options.nativeAdmin('generations.check',{commandId},context);
  else if(operation==='updates.rollback')result=await this.options.nativeAdmin('generations.rollback',{commandId,expectedCurrent:args.expectedCurrent},context);
  else if(operation==='updates.runtime.repair')result=await this.options.nativeAdmin('generations.repair',{...args,commandId},context);
  else if(operation==='updates.runtime.select')result=await this.options.nativeAdmin('generations.promote',{...args,commandId},context);
  else {
   const current=await this.inspect({},context);
   result=await this.options.nativeAdmin('generations.prepare',{commandId:commandId+':prepare',...(args.checkId?{checkId:args.checkId}:{})},context);
   preparationReturned=true;
   // A known successful preparation is the only permission to attempt selection.
   if(result.state==='succeeded')result=await this.options.nativeAdmin('generations.promote',{commandId:commandId+':promote',generation:result.result.generation,expectedCurrent:current.pointer?.current??null},context);
  }
  }catch(error){
   // A later promotion refusal cannot erase successful preparation. Reads and
   // receipt inspection above never settle a different, original command.
   if(!preparationReturned&&generationsUnavailable(error))return {
    accepted:false,result:{commandId,operation,state:'failed',executed:false,applied:false,replayed:false,
     reason:'native-generations-unavailable',message:'Native runtime updates are turned off for this installation. No update was started.'},
    updates:[],invalidate:[],_meta:{'amplifier.dev/operation':{id:commandId}},
   };
   throw error;
  }
  this.cached=undefined;this.revision++;this.options.onInvalidate?.('maintenance','host');
  return {accepted:true,result,updates:[],invalidate:['maintenance']};
 }
 async close(){this.closed=true;this.cached=undefined;}
}
export function createMaintenanceCapabilities(options:Options){return new MaintenanceCapabilities(options);}
