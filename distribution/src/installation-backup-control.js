import {createServer,request as httpRequest} from 'node:http';
import {constants} from 'node:fs';
import {open,realpath,lstat,rename,unlink} from 'node:fs/promises';
import {dirname,isAbsolute} from 'node:path';
import {randomUUID,timingSafeEqual} from 'node:crypto';

const LIMIT=1024*1024;
const operations=new Set(['inspect','review','submit','receipt','reconcile','readArchive']);
const code=e=>/^[a-z_]{1,100}$/.test(e?.message)?e.message:'backup_control_unconfirmed';
async function privateFile(path){
 if(!isAbsolute(path)||await realpath(dirname(path))!==dirname(path))throw Error('backup_control_private_path_required');
 const dir=await lstat(dirname(path));if(!dir.isDirectory()||dir.mode&0o077)throw Error('backup_control_private_path_required');
 const h=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
 try{const s=await h.stat();if(!s.isFile()||s.nlink!==1||s.mode&0o077||s.size>4096)throw Error('backup_control_private_file_required');return await h.readFile('utf8');}finally{await h.close();}
}
async function tokenAt(path){const value=(await privateFile(path)).trim();if(!/^[a-f0-9]{64}$/.test(value))throw Error('backup_control_token_invalid');return value;}
const authenticated=(got,expected)=>typeof got==='string'&&Buffer.byteLength(got)===Buffer.byteLength(expected)&&timingSafeEqual(Buffer.from(got),Buffer.from(expected));
async function publish(path,value){
 if(!isAbsolute(path)||await realpath(dirname(path))!==dirname(path)||(await lstat(dirname(path))).mode&0o077)throw Error('backup_control_private_path_required');
 const tmp=path+'.'+randomUUID(),h=await open(tmp,'wx',0o600);
 try{await h.writeFile(JSON.stringify(value)+'\n');await h.sync();}finally{await h.close();}
 await rename(tmp,path);
 const d=await open(dirname(path),constants.O_RDONLY);try{await d.sync();}finally{await d.close();}
}
function validate(input){
 if(!input||typeof input!=='object'||Array.isArray(input)||!operations.has(input.operation)||Object.keys(input).some(k=>!['operation','commandId','args'].includes(k)))throw Error('backup_control_request_invalid');
 if(['inspect','review'].includes(input.operation)&&Object.keys(input).length!==1)throw Error('backup_control_request_invalid');
 if(['submit','receipt','reconcile','readArchive'].includes(input.operation)&&!(typeof input.commandId==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$/.test(input.commandId)))throw Error('backup_control_request_invalid');
 if(['submit','readArchive'].includes(input.operation)?(!input.args||typeof input.args!=='object'||Array.isArray(input.args)):input.args!==undefined)throw Error('backup_control_request_invalid');
 if(input.operation==='readArchive'&&Object.keys(input.args).some(k=>!['sha256','offset','maxBytes'].includes(k)))throw Error('backup_control_request_invalid');
 return input;
}

/** Private loopback control, owned by the external installation process. It
 * never accepts filesystem paths, modules, writer proofs, or replay requests. */
export async function serveInstallationBackupControl({coordinator,discoveryFile,tokenFile,installationId,dataScope}){
 if(coordinator?.outlivesDistribution!==true||!installationId||!dataScope)throw Error('external_backup_controller_required');
 const auth='Bearer '+await tokenAt(tokenFile),instanceId=randomUUID();
 const server=createServer(async(req,res)=>{
  const send=(status,value)=>{if(res.destroyed)return;const body=JSON.stringify(value);if(Buffer.byteLength(body)>LIMIT){res.writeHead(500);res.end('{"error":"backup_control_response_limit"}');return;}res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(body);};
  if(req.method!=='POST'||req.url!=='/backup'||req.headers.origin||!authenticated(req.headers.authorization,auth)){send(403,{error:'backup_control_denied'});req.resume();return;}
  try{
   let bytes=0;const chunks=[];for await(const chunk of req){bytes+=chunk.length;if(bytes>8192)throw Error('backup_control_request_limit');chunks.push(chunk);}
   const input=validate(JSON.parse(Buffer.concat(chunks).toString('utf8')));
   const result=await coordinator[input.operation](input.commandId,input.args);
   send(200,{instanceId,installationId,dataScope,result});
  }catch(error){send(400,{error:code(error),workReplayed:false});}
 });
 server.requestTimeout=15000;server.headersTimeout=15000;
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 try{await publish(discoveryFile,{schema:'unified-backup-control-v1',host:'127.0.0.1',port:server.address().port,instanceId,installationId,dataScope,tokenFile});}
 catch(error){await new Promise(resolve=>server.close(resolve));throw error;}
 return {async close(){await new Promise(resolve=>server.close(resolve));try{const current=JSON.parse(await privateFile(discoveryFile));if(current.instanceId===instanceId)await unlink(discoveryFile);}catch(e){if(e.code!=='ENOENT')throw e;}}};
}

/** Reads the current private endpoint for every call. Requests are never
 * automatically retried. A lost submission is inspected by its original ID. */
export function connectInstallationBackupControl({discoveryFile,installationId,dataScope}){
 let closed=false,timer=null,last=null,polling=false;const listeners=new Set();
 async function call(operation,commandId,args){
  if(closed)throw Error('backup_control_closed');
  const input={operation,...(commandId===undefined?{}:{commandId}),...(args===undefined?{}:{args})};validate(input);
  const endpoint=JSON.parse(await privateFile(discoveryFile));
  if(endpoint.schema!=='unified-backup-control-v1'||endpoint.host!=='127.0.0.1'||!Number.isInteger(endpoint.port)||endpoint.port<1||endpoint.port>65535||endpoint.installationId!==installationId||endpoint.dataScope!==dataScope)throw Error('backup_control_binding_conflict');
  const auth='Bearer '+await tokenAt(endpoint.tokenFile),body=JSON.stringify(input);
  return new Promise((resolve,reject)=>{
   const req=httpRequest({host:endpoint.host,port:endpoint.port,path:'/backup',method:'POST',headers:{authorization:auth,'content-type':'application/json','content-length':Buffer.byteLength(body)}},res=>{
    const chunks=[];let bytes=0;
    res.on('data',chunk=>{bytes+=chunk.length;if(bytes>LIMIT){res.destroy();reject(Error('backup_control_response_limit'));return;}chunks.push(chunk);});
    res.on('error',reject);res.on('end',()=>{try{const value=JSON.parse(Buffer.concat(chunks));if(res.statusCode!==200)throw Error(code({message:value.error}));if(value.instanceId!==endpoint.instanceId||value.installationId!==installationId||value.dataScope!==dataScope)throw Error('backup_control_binding_conflict');resolve(value.result);}catch(error){reject(error);}});
   });
   req.setTimeout(15000,()=>req.destroy(Error('backup_control_timeout')));req.on('error',reject);req.end(body);
  });
 }
 const poll=async()=>{if(polling||closed)return;polling=true;try{const next=JSON.stringify(await call('inspect'));if(next!==last){last=next;for(const f of listeners)try{f();}catch{}}}catch{}finally{polling=false;if(!closed&&listeners.size){timer=setTimeout(poll,2000);timer.unref?.();}}};
 return {outlivesDistribution:true,inspect:()=>call('inspect'),review:()=>call('review'),submit:(id,args)=>call('submit',id,args),receipt:id=>call('receipt',id),reconcile:id=>call('reconcile',id),
  readArchive:(id,args)=>call('readArchive',id,args),
  subscribe:f=>{listeners.add(f);if(listeners.size===1)void poll();return()=>{listeners.delete(f);if(!listeners.size){clearTimeout(timer);timer=null;}};},
  close(){closed=true;clearTimeout(timer);listeners.clear();}};
}
