import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
const callbacks=new Set(['attachmentMetadata','attachmentPage','readExport']);
function parseOwnerFrame(line){
 const row=JSON.parse(line);
 if(!row||typeof row!=='object'||Array.isArray(row)||row.jsonrpc!=='2.0')throw Error('Malformed owner envelope');
 if(row.method!==undefined){
  if(typeof row.method!=='string'||!row.params||typeof row.params!=='object'||Array.isArray(row.params))throw Error('Malformed owner notification or callback');
  if(!['owner/idle','owner/changed'].includes(row.method)&&!['string','number'].includes(typeof row.id))throw Error('Malformed owner callback identity');
 }else if(!Number.isSafeInteger(row.id)||Object.hasOwn(row,'result')===Object.hasOwn(row,'error')||(Object.hasOwn(row,'error')&&(!row.error||typeof row.error!=='object'||typeof row.error.message!=='string'||!Number.isInteger(row.error.code))))throw Error('Malformed owner reply');
 return row;
}
function ownerTimeout(launcher,initialize=false){
 const value=initialize?(launcher.initializeTimeoutMs??15000):(launcher.requestTimeoutMs??90000);
 if(!Number.isInteger(value)||value<25||value>90000)throw Error('Owner timeout must be25–90000ms');
 return value;
}
export class Connection{
 constructor(launch,host,changed,idle=()=>{}){this.idle=idle;this.launch=launch;this.host=host;this.changed=changed;this.pending=new Map();this.next=0;this.closed=false;this.requests=0;this.callbacks=0;}
 get admissionPending(){return !!(this.closed||this.requests||this.callbacks||this.pending.size);}
 fail(message){this.closed=true;for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error(message));}this.pending.clear();}
 write(row){const line=JSON.stringify(row)+'\n';if(Buffer.byteLength(line)>4_000_000)throw Error('Feedback frame exceeds 4MB');if(this.closed||!this.child)throw Error('Feedback owner disconnected; uncertain work was not replayed');this.child.stdin.write(line,error=>{if(error)this.fail('Feedback owner disconnected; uncertain work was not replayed')});}
 send(method,params){if(this.pending.size>=64)throw Error('Owner request capacity reached');const timeout=ownerTimeout(this.launch,method==='initialize'),id=++this.next;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.fail('Owner reply timed out; outcome unknown, not replayed');this.child?.kill();},timeout);timer.unref();this.pending.set(id,{resolve,reject,timer});try{this.write({jsonrpc:'2.0',id,method,params})}catch(error){clearTimeout(timer);this.pending.delete(id);reject(error)}});}

 async receive(line){let row;try{row=parseOwnerFrame(line)}catch{this.fail('Invalid Feedback response');this.child.kill();return;}
  if(row.method==='owner/idle'){this.idle();return;}
  if(row.method==='owner/changed'){this.changed();return;}
  if(row.method){this.callbacks++;try{const method=String(row.method).replace(/^host\//,'');if(!String(row.method).startsWith('host/')||!callbacks.has(method))throw Error('Unknown authority callback');const result=await this.host(method,row.params||{});this.write({jsonrpc:'2.0',id:row.id,result:result??null})}catch(error){if(!this.closed)this.write({jsonrpc:'2.0',id:row.id,error:{code:-32000,message:error.message}})}finally{this.callbacks--;}return;}
  const pending=this.pending.get(row.id);if(!pending)return;this.pending.delete(row.id);clearTimeout(pending.timer);row.error?pending.reject(Object.assign(Error(row.error.message),row.error.data||{})):pending.resolve(row.result);
 }
 async request(method,params){this.requests++;try{return await this.performRequest(method,params);}finally{this.requests--;}}
 async performRequest(method,params){if(this.closed)throw Error('Feedback owner closed; no replay');if(!this.ready)this.ready=(async()=>{this.child=spawn(this.launch.command,this.launch.args||[],{cwd:this.launch.cwd,env:{...process.env,...this.launch.env},stdio:'pipe'});this.child.stdin.on('error',()=>{this.fail('Owner input transport failed; outcome unknown');this.child.kill();});this.child.stderr.on('data',()=>{});this.child.on('error',()=>this.fail('Feedback owner failed to start'));this.child.on('exit',()=>this.fail('Feedback owner exited; no replay'));let bytes=0;this.child.stdout.on('data',chunk=>{for(const byte of chunk){bytes=byte===10?0:bytes+1;if(bytes>4_000_000){this.fail('Feedback frame exceeded limit');this.child.kill();return;}}});createInterface({input:this.child.stdout}).on('line',line=>{void this.receive(line).catch(()=>{this.fail('Malformed owner response; outcome unknown, not replayed');this.child.kill();});});const initial=await this.send('initialize',{});if(initial.protocolVersion!==1)throw Error('Unsupported Feedback owner')})().catch(error=>{this.fail('Feedback owner initialization failed; no replay');this.child?.kill();throw error;});await this.ready;return this.send(method,params);}
 async close(){
  const child=this.child;this.fail('Owner closed; uncertain work was not replayed');
  if(!child||child.exitCode!==null||child.signalCode!==null)return;
  child.stdin.end();await new Promise(resolve=>{const timer=setTimeout(()=>{child.kill('SIGKILL');resolve()},4000);child.once('close',()=>{clearTimeout(timer);resolve()})});
 }
}
