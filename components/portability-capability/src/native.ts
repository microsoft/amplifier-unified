import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {Readable,Writable,Transform} from 'node:stream';
import {ClientSideConnection,ndJsonStream} from '@agentclientprotocol/sdk';
import type {Launcher,Json,FenceContext,ReleaseProof,Participant} from './index.js';
/** Private passive peer. Its process-local fence complements the shared home admin gate. */
export class TransferConnection {
 private process?:ChildProcessWithoutNullStreams;private client?:ClientSideConnection;private starting?:Promise<void>;private closed=false;private supported=false;private calls=0;
 private held?:{context:FenceContext;phase:'checking'|'held'|'unknown'};
 constructor(private launcher:Launcher){for(const value of [launcher.initializeTimeoutMs,launcher.requestTimeoutMs])if(value!==undefined&&(!Number.isInteger(value)||value<25||value>120000))throw Error('Native transfer timeout must be25–120000ms');}
 private idle(){try{this.launcher.onMayBeIdle?.();}catch{/* Advisory only. */}}
 private async bounded<T>(work:Promise<T>,timeoutMs:number):Promise<T>{let timer:NodeJS.Timeout;try{return await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Native transfer reply timed out; outcome unknown and never replayed')),timeoutMs);timer.unref();})]);}finally{clearTimeout(timer!);}}
 private start(){
  if(this.closed)throw Error('Native transfer connection closed; no unknown operation replay');
  if(!this.starting)this.starting=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   const lost=()=>{this.closed=true;if(this.held)this.held.phase='unknown';};child.stderr.on('data',()=>{});child.on('exit',lost);child.on('error',lost);
   let bytes=0;const guard=new Transform({transform(chunk:Buffer,_encoding,callback){for(const byte of chunk){bytes=byte===10?0:bytes+1;if(bytes>2*1024*1024){callback(Error('Native transfer frame exceeds2MiB'));return;}}callback(null,chunk);}});
   guard.on('error',()=>child.kill());child.stdout.pipe(guard);
   this.client=new ClientSideConnection(()=>({sessionUpdate:async()=>{throw Error('Passive transfer peer cannot execute');},requestPermission:async()=>({outcome:{outcome:'cancelled' as const}})}),ndJsonStream(Writable.toWeb(child.stdin) as WritableStream<Uint8Array>,Readable.toWeb(guard) as ReadableStream<Uint8Array>));
   const init=await this.bounded(this.client.initialize({protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}}),this.launcher.initializeTimeoutMs??15000);
   const transfer=(init.agentCapabilities?._meta?.['amplifier.dev/native'] as Json)?.transfer;
   if(transfer?.version!==1)throw Error('Native agent lacks launcher-enabled transfer authority');
   this.supported=transfer.lifecycle?.version===1&&transfer.lifecycle?.heldIntake===true&&transfer.lifecycle?.nativeAdminWriter===true;
  })();return this.starting;
 }
 private rpc(method:string,params:Json){let timer:NodeJS.Timeout;return Promise.race([this.client!.extMethod(method,params) as Promise<Json>,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{if(this.held)this.held.phase='unknown';reject(Error('Native transfer reply timed out; outcome unknown and never replayed'));},this.launcher.requestTimeoutMs??120000);timer.unref();})]).finally(()=>clearTimeout(timer));}
 perform=async(args:Json)=>{
  if(this.held)throw Object.assign(Error('Native transfer intake is held; no new effect admitted'),{executed:false});
  this.calls++;try{await this.start();return await this.rpc('_amplifier/transfer',{sessionId:args.nativeSessionId,cwd:args.historyHome,operation:args.operation,args:args.args??{}});}finally{this.calls--;if(!this.calls)this.idle();}
 };
 private context(value:Readonly<FenceContext>):FenceContext{const result={} as FenceContext;for(const key of ['fenceId','commandId','purpose','instanceId','dataScope'] as const){if(typeof value[key]!=='string'||!value[key]||value[key].length>200||/[\x00-\x1f]/.test(value[key]))throw Error('Bounded native transfer fence required');(result as Json)[key]=value[key];}return result;}
 inspectQuiescence=async()=>{await this.start();if(!this.supported)return {supported:false};return this.rpc('_amplifier/transfer/lifecycle',{operation:'inspect',args:{}});};
 private acquire=async(value:Readonly<FenceContext>)=>{
  const context=this.context(value);if(this.calls)return null;if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Native transfer already belongs to another fence');
  this.held={context,phase:'checking'};
  try{await this.start();if(!this.supported){this.held=undefined;return null;}
   const result=await this.rpc('_amplifier/transfer/lifecycle',{operation:'acquire',args:context});
   if(result.acquired!==true){if(result.executed===false&&!result.intakeClosed){this.held=undefined;return null;}throw Error('Native transfer acquisition is uncertain');}
   if(!result.processId||JSON.stringify(this.context(result.fence))!==JSON.stringify(context)||!result.intakeClosed)throw Error('Native transfer did not confirm exact process fence');
   this.held.phase='held';return {ownerId:this.launcher.ownerId??'native-transfer',fenceId:context.fenceId,release:(outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>this.release(context,outcome,proof)};
  }catch(error){if(this.held)this.held.phase='unknown';throw error;}
 };
 private release=async(value:Readonly<FenceContext>,outcome:'unchanged'|'ready'|'unknown',proof?:ReleaseProof|{kind:'admission-refused'})=>{
  const context=this.context(value);if(this.calls)throw Error('Native transfer requests remain active');
  if(this.held&&JSON.stringify(this.held.context)!==JSON.stringify(context))throw Error('Native transfer release belongs to another fence');
  // Replacement only reserves a passive process. The aggregate durable owner and
  // home admin participant still authenticate old effects and cross-process idle.
  if(!this.starting){const lease=await this.acquire(context);if(!lease)throw Error('Replacement native transfer lacks held proof');}
  this.held={context,phase:'unknown'};await this.start();if(!this.supported)throw Error('Native transfer lacks negotiated retirement proof');
  const result=await this.rpc('_amplifier/transfer/lifecycle',{operation:'release',args:{...context,outcome,proof}});
  if(outcome==='unknown')return;if(result.released!==true||result.intakeClosed!==false)throw Error('Native transfer release remains uncertain');this.held=undefined;
 };
 get quiescenceParticipant():Participant{return {id:this.launcher.ownerId??'native-transfer',acquire:this.acquire,reconcileRelease:context=>this.release(context,context.outcome,context.proof)};}
 async close(){this.closed=true;if(this.held)this.held.phase='unknown';if(!this.process||this.process.exitCode!==null||this.process.signalCode!==null)return;this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},3000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});}
}
