import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {Readable,Writable,Transform} from 'node:stream';
import {ClientSideConnection,ndJsonStream} from '@agentclientprotocol/sdk';
import type {Launcher,Json,FenceContext,ReleaseProof,Participant} from './index.js';
import {serviceIdentity,validateServiceRelease,evidenceKey} from './service-lifecycle.js';
import {abortProof,abortReceipt,type AdmissionAbortContext} from './admission-abort.js';
/** Private passive peer. Its process-local fence complements the shared home admin gate. */
export class TransferConnection {
 private process?:ChildProcessWithoutNullStreams;private client?:ClientSideConnection;private starting?:Promise<void>;private closed=false;private supported=false;private serviceStopSupported=false;private abortSupported=false;private calls=0;private pending=new Set<Promise<any>>();
 private originalAdmission?:{context:FenceContext;result:Json};private held?:{context:FenceContext;phase:'checking'|'held'|'unknown'};private releases=new Map<string,string>();
 constructor(private launcher:Launcher){for(const value of [launcher.initializeTimeoutMs,launcher.requestTimeoutMs])if(value!==undefined&&(!Number.isInteger(value)||value<25||value>120000))throw Error('Native transfer timeout must be25–120000ms');}
 private idle(){try{this.launcher.onMayBeIdle?.();}catch{/* Advisory only. */}}
 private async bounded<T>(work:Promise<T>,timeoutMs:number):Promise<T>{let timer:NodeJS.Timeout;this.pending.add(work);const settled=()=>{this.pending.delete(work);if(!this.calls&&!this.pending.size)this.idle();};void work.then(settled,settled);try{return await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Native transfer reply timed out; outcome unknown and never replayed')),timeoutMs);timer.unref();})]);}finally{clearTimeout(timer!);}}
 private start(){
  if(this.closed)throw Error('Native transfer connection closed; no unknown operation replay');
  if(!this.starting)this.starting=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   const lost=()=>{this.closed=true;this.pending.clear();if(this.held)this.held.phase='unknown';};child.stderr.on('data',()=>{});child.on('exit',lost);child.on('error',lost);
   let bytes=0;const guard=new Transform({transform(chunk:Buffer,_encoding,callback){for(const byte of chunk){bytes=byte===10?0:bytes+1;if(bytes>2*1024*1024){callback(Error('Native transfer frame exceeds2MiB'));return;}}callback(null,chunk);}});
   guard.on('error',()=>child.kill());child.stdout.pipe(guard);
   this.client=new ClientSideConnection(()=>({sessionUpdate:async()=>{throw Error('Passive transfer peer cannot execute');},requestPermission:async()=>({outcome:{outcome:'cancelled' as const}})}),ndJsonStream(Writable.toWeb(child.stdin) as WritableStream<Uint8Array>,Readable.toWeb(guard) as ReadableStream<Uint8Array>));
   const init=await this.bounded(this.client.initialize({protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}}),this.launcher.initializeTimeoutMs??15000);
   const transfer=(init.agentCapabilities?._meta?.['amplifier.dev/native'] as Json)?.transfer;
   if(transfer?.version!==1)throw Object.assign(Error('Native agent lacks launcher-enabled transfer authority'),{code:'native_transfer_unavailable',executed:false,intakeClosed:false});
   this.supported=transfer.lifecycle?.version===1&&transfer.lifecycle?.heldIntake===true&&transfer.lifecycle?.nativeAdminWriter===true;this.serviceStopSupported=transfer.lifecycle?.serviceStop?.version===1;this.abortSupported=transfer.lifecycle?.admissionAbort?.version===1;
  })();return this.starting;
 }
 private rpc(method:string,params:Json,affectsHeld=true){return this.bounded(this.client!.extMethod(method,params) as Promise<Json>,this.launcher.requestTimeoutMs??120000).catch(error=>{if(affectsHeld&&this.held)this.held.phase='unknown';throw error;});}
 perform=async(args:Json)=>{
  if(this.held)throw Object.assign(Error('Native transfer intake is held; no new effect admitted'),{executed:false});
  this.calls++;try{await this.start();return await this.rpc('_amplifier/transfer',{sessionId:args.nativeSessionId,cwd:args.historyHome,operation:args.operation,args:args.args??{}});}finally{this.calls--;if(!this.calls&&!this.pending.size)this.idle();}
 };
 private context(value:Readonly<FenceContext>):FenceContext{const result={} as FenceContext;for(const key of ['fenceId','commandId','purpose','instanceId','dataScope'] as const){if(typeof value[key]!=='string'||!value[key]||value[key].length>200||/[\x00-\x1f]/.test(value[key]))throw Error('Bounded native transfer fence required');(result as Json)[key]=value[key];}if(value.purpose==='service-stop'){result.serviceIdentity=serviceIdentity(value.serviceIdentity);if(result.serviceIdentity.instanceId!==value.instanceId||result.serviceIdentity.dataScope!==value.dataScope)throw Error('Service identity must bind native transfer fence');}else if(value.serviceIdentity)throw Error('Service identity requires service-stop');return result;}
 inspectQuiescence=async()=>{await this.start();if(!this.supported)return {supported:false};return this.rpc('_amplifier/transfer/lifecycle',{operation:'inspect',args:{}});};
 preflightQuiescence=async(value:Readonly<FenceContext>)=>{const context=this.context(value);await this.start();return this.supported&&(context.purpose!=='service-stop'||this.serviceStopSupported)&&(context.purpose!=='distribution-update'||this.abortSupported);};
 private available(context:FenceContext){if(this.calls)return false;if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Native transfer already belongs to another fence');return !this.held;}
 private acquire=async(value:Readonly<FenceContext>)=>{
  const context=this.context(value);const durable=context.purpose==='distribution-update';
  if(durable){if(this.held&&(JSON.stringify(this.held.context)!==JSON.stringify(context)||this.held.phase==='checking'))throw Error('Native transfer admission is already pending or belongs to another fence');}
  else if(!this.available(context))return null;
  if(!await this.preflightQuiescence(context))return null;
  // Another caller may have acquired while passive initialize was pending.
  if(!durable&&!this.available(context))return null;
  if(durable&&this.pending.size)throw Error('Original native transfer RPC is still pending; no acquisition repeated');
  this.held={context,phase:'checking'};
  try{
   const result=await this.rpc('_amplifier/transfer/lifecycle',{operation:'acquire',args:{...context,...(durable?{ownerId:this.launcher.ownerId??'native-transfer'}:{})}});
   if(durable){if(result.ownerId!==(this.launcher.ownerId??'native-transfer')||!result.receiptId||['commandId','fenceId','instanceId','dataScope'].some(k=>result[k]!==context[k as keyof FenceContext]))throw Error('Native transfer lacks original durable disposition receipt');this.originalAdmission={context,result};}
   if(result.acquired!==true){if(result.executed===false&&!result.intakeClosed){this.held=undefined;return null;}throw Error('Native transfer acquisition is uncertain');}
   if(!result.processId||JSON.stringify(this.context(result.fence))!==JSON.stringify(context)||!result.intakeClosed)throw Error('Native transfer did not confirm exact process fence');
   this.held.phase='held';return {ownerId:this.launcher.ownerId??'native-transfer',fenceId:context.fenceId,...(durable?{acquisitionReceipt:result}:{}),release:(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.release(context,outcome,proof,true)};
  }catch(error){if(this.held)this.held.phase='unknown';throw error;}
 };
 private legacyDistributionReserve=async(context:FenceContext)=>{
  if(this.calls||this.pending.size)throw Error('Native transfer requests remain active');
  this.held={context,phase:'checking'};
  const result=await this.rpc('_amplifier/transfer/lifecycle',{operation:'acquire',args:context});
  if(result.acquired!==true||!result.processId||!result.intakeClosed||JSON.stringify(this.context(result.fence))!==JSON.stringify(context))throw Error('Replacement legacy transfer lacks its ordinary held proof');
  this.held.phase='held';
 };
 private release=async(value:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'},liveRollback=false,legacyDistribution=false)=>{
  const context=this.context(value),evidence=evidenceKey({context,outcome,proof}),prior=this.releases.get(context.fenceId);if(prior!==undefined){if(prior!==evidence||this.held)throw Error('Transfer release receipt binds different evidence');return;}if(this.calls||this.pending.size)throw Error('Native transfer requests remain active');
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Native transfer release belongs to another fence');
  const rollback=liveRollback&&this.held?.phase==='held'&&outcome==='unchanged'&&proof&&Object.keys(proof).length===1&&'kind' in proof&&proof.kind==='admission-refused';
  if(context.purpose==='service-stop'&&outcome!=='unknown'&&!rollback)validateServiceRelease(context,outcome,proof);
  if(legacyDistribution){const p=proof as ReleaseProof;if(context.purpose!=='distribution-update'||!p||p.verified!==true||p.outcome!==outcome||['commandId','fenceId','dataScope'].some(k=>p[k as keyof ReleaseProof]!==context[k as keyof FenceContext])||!p.receiptId||typeof p.instanceId!=='string'||(p.instanceId===context.instanceId)!==(outcome==='unchanged'))throw Error('Exact ordinary authenticated legacy lifecycle release proof required');}
  await this.start();if(!this.supported)throw Error('Native transfer lacks negotiated retirement proof');
  const durable=context.purpose==='distribution-update'&&this.abortSupported&&!legacyDistribution;
  // Replacement only reserves a passive process. The aggregate durable owner and
  // home admin participant still authenticate old effects and cross-process idle.
  if(!this.held&&!durable){if(context.purpose==='distribution-update')await this.legacyDistributionReserve(context);else{const lease=await this.acquire(context);if(!lease)throw Error('Replacement native transfer lacks held proof');}}
  this.held={context,phase:'unknown'};
  const result=await this.rpc('_amplifier/transfer/lifecycle',{operation:'release',args:{...context,outcome,proof,...(durable?{ownerId:this.launcher.ownerId??'native-transfer'}:{})}});
  if(outcome==='unknown')return;if(result.released!==true||(durable?(result.ownerIntakeClosed!==false||result.ownerId!==(this.launcher.ownerId??'native-transfer')||['commandId','fenceId','instanceId','dataScope'].some(k=>result[k]!==context[k as keyof FenceContext])):result.intakeClosed!==false))throw Error('Native transfer release remains uncertain');this.releases.set(context.fenceId,evidence);if(this.releases.size>256)this.releases.delete(this.releases.keys().next().value!);this.held=undefined;
 };
 private abortAdmission=async(value:AdmissionAbortContext)=>{
  const context=this.context(value),proof=abortProof(value),ownerId=this.launcher.ownerId??'native-transfer';
  if(this.calls||this.pending.size)throw Error('Native transfer actual RPC work remains pending; abort stays closed');
  await this.start();if(!this.abortSupported||!this.supported)throw Error('Native transfer lacks original admission abort authority');
  if(this.calls||this.pending.size)throw Error('Native transfer actual RPC work remains pending; abort stays closed');
  const selected=!!this.held&&JSON.stringify(this.held.context)===JSON.stringify(context);
  const result=abortReceipt(await this.rpc('_amplifier/transfer/lifecycle',{operation:'abortAdmission',args:{...context,ownerId,proof}},selected),context,ownerId);
  if(selected)this.held=undefined;return result;
 };
 get quiescenceParticipant():Participant{return {id:this.launcher.ownerId??'native-transfer',serviceStop:{version:1 as const},preflight:this.preflightQuiescence,hasPendingAdmissionWork:()=>!!(this.calls||this.pending.size),admissionEvidence:context=>this.originalAdmission&&JSON.stringify(this.originalAdmission.context)===JSON.stringify(this.context(context))?this.originalAdmission.result:undefined,acquire:this.acquire,abortAdmission:this.abortAdmission,reconcileRelease:context=>this.release(context,context.outcome,context.proof,false,context.legacyDistribution===true)};}
 async close(){this.closed=true;if(this.held)this.held.phase='unknown';if(!this.process||this.process.exitCode!==null||this.process.signalCode!==null)return;this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},3000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});}
}
