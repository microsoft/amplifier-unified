import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {Readable,Writable,Transform} from 'node:stream';
import {ClientSideConnection,ndJsonStream} from '@agentclientprotocol/sdk';
import type {Launcher,Json} from './index.js';
/** Independent launcher-authorized passive native peer. Never opens an agent session. */
export class TransferConnection {
 private process?:ChildProcessWithoutNullStreams;private client?:ClientSideConnection;private starting?:Promise<void>;private closed=false;
 constructor(private launcher:Launcher){}
 private start(){
  if(this.closed)throw Error('Native transfer connection closed; no unknown operation replay');
  if(!this.starting)this.starting=(async()=>{
   const child=this.process=spawn(this.launcher.command,this.launcher.args??[],{cwd:this.launcher.cwd,env:{...process.env,...this.launcher.env},stdio:'pipe'});
   child.stderr.on('data',()=>{});child.on('exit',()=>{this.closed=true;});child.on('error',()=>{this.closed=true;});
   let bytes=0;const guard=new Transform({transform(chunk:Buffer,_encoding,callback){for(const byte of chunk){bytes=byte===10?0:bytes+1;if(bytes>2*1024*1024){callback(Error('Native transfer frame exceeds2MiB'));return;}}callback(null,chunk);}});
   guard.on('error',()=>child.kill());child.stdout.pipe(guard);
   this.client=new ClientSideConnection(()=>({sessionUpdate:async()=>{throw Error('Passive transfer peer cannot execute');},requestPermission:async()=>({outcome:{outcome:'cancelled' as const}})}),ndJsonStream(Writable.toWeb(child.stdin) as WritableStream<Uint8Array>,Readable.toWeb(guard) as ReadableStream<Uint8Array>));
   const init=await this.client.initialize({protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}});
   if((init.agentCapabilities?._meta?.['amplifier.dev/native'] as Json)?.transfer?.version!==1)throw Error('Native agent lacks launcher-enabled transfer authority');
  })();return this.starting;
 }
 perform=async(args:Json)=>{await this.start();return this.client!.extMethod('_amplifier/transfer',{sessionId:args.nativeSessionId,cwd:args.historyHome,operation:args.operation,args:args.args??{}}) as Promise<Json>;};
 async close(){this.closed=true;if(!this.process||this.process.exitCode!==null)return;this.process.stdin.end();await new Promise<void>(resolve=>{const timer=setTimeout(()=>{this.process?.kill();resolve();},3000);this.process!.once('exit',()=>{clearTimeout(timer);resolve();});});}
}
