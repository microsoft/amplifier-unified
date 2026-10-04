// Bundled into the private setup file. Only Node built-ins; no host/runtime imports.
import * as fs from 'node:fs/promises';
import {createReadStream,constants} from 'node:fs';
import {createHash,randomUUID} from 'node:crypto';
import {request} from 'node:https';
import {spawn} from 'node:child_process';
import {createGunzip} from 'node:zlib';
import {join,resolve,dirname,relative,isAbsolute,posix} from 'node:path';
import {homedir} from 'node:os';
import {fileURLToPath} from 'node:url';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const safeError=code=>Object.assign(new Error(code),{code});
const inside=(root,path)=>{const r=relative(root,path);return r===''||r!=='..'&&!r.startsWith('../')&&!r.startsWith('..\\')&&!isAbsolute(r);};
const q=s=>"'"+s.replaceAll("'","'\\''")+"'";
const atLeast=(actual,minimum)=>{const a=actual.split('.').map(Number),b=minimum.split('.').map(Number);for(let i=0;i<Math.max(a.length,b.length);i++){if((a[i]??0)!==(b[i]??0))return(a[i]??0)>(b[i]??0);}return true;};
async function platformCheck(profile){const t=profile.artifact.target,os=process.platform==='darwin'?'macos':process.platform,arch=process.arch==='x64'?'x86_64':process.arch;if(t.os!==os||t.arch!==arch)throw safeError('client-platform-mismatch');
 if(os==='macos'&&(!t.osFloor||!atLeast(await run('/usr/bin/sw_vers',['-productVersion']),t.osFloor)))throw safeError('client-os-floor-not-met');
 if(os==='linux'){const glibc=process.report.getReport().header.glibcVersionRuntime;if(!glibc||!profile.artifact.minimumGlibc||!atLeast(glibc,profile.artifact.minimumGlibc))throw safeError('client-glibc-floor-not-met');}
}
async function syncDirectory(path){const h=await fs.open(path,'r');try{await h.sync();}finally{await h.close();}}
async function atomic(path,bytes,mode=0o600){const temporary=path+'.'+randomUUID();const h=await fs.open(temporary,'wx',mode);try{await h.writeFile(bytes);await h.sync();}finally{await h.close();}await fs.rename(temporary,path);await syncDirectory(dirname(path));}
async function exclusive(path,bytes){const h=await fs.open(path,'wx',0o600);try{await h.writeFile(bytes);await h.sync();}finally{await h.close();}await syncDirectory(dirname(path));}
async function privateDirectory(path){await fs.mkdir(path,{recursive:true,mode:0o700});const s=await fs.lstat(path);if(!s.isDirectory()||s.isSymbolicLink()||s.uid!==process.getuid()||(s.mode&0o077))throw safeError('private-install-directory-required');}
async function hashFile(path){const h=createHash('sha256');for await(const chunk of createReadStream(path))h.update(chunk);return h.digest('hex');}
function run(file,args,env={}){return new Promise((resolve,reject)=>{let bytes=0,text='';const child=spawn(file,args,{env:{PATH:process.env.PATH,HOME:homedir(),LANG:'C.UTF-8',PYTHONDONTWRITEBYTECODE:'1',...env},stdio:['ignore','pipe','pipe']});const timer=setTimeout(()=>child.kill('SIGKILL'),120000);for(const stream of [child.stdout,child.stderr])stream.on('data',data=>{bytes+=data.length;if(bytes>1024*1024)child.kill('SIGKILL');else text+=data;});child.once('error',()=>{clearTimeout(timer);reject(safeError('client-validation-failed'));});child.once('close',code=>{clearTimeout(timer);code===0&&bytes<=1024*1024?resolve(text.trim()):reject(safeError('client-validation-failed'));});});}
function requestBytes(url,{body,ca,maxBytes=65536}={}){return new Promise((resolve,reject)=>{const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password||u.hash)return reject(safeError('verified-https-required'));const req=request(u,{method:body?'POST':'GET',rejectUnauthorized:true,ca:ca||undefined,headers:body?{'Content-Type':'application/json','Content-Length':body.length}:{},timeout:30000},response=>{
 if(response.statusCode>=300&&response.statusCode<400){response.resume();return reject(safeError('credential-redirect-refused'));}
 const chunks=[];let size=0;response.on('data',chunk=>{size+=chunk.length;if(size>maxBytes)response.destroy(safeError('response-bound-exceeded'));else chunks.push(chunk);});response.on('end',()=>resolve({status:response.statusCode,bytes:Buffer.concat(chunks)}));response.on('error',()=>reject(safeError('enrollment-response-unconfirmed')));
 });const deadline=setTimeout(()=>req.destroy(),30000);req.once('close',()=>clearTimeout(deadline));req.on('timeout',()=>req.destroy());req.on('error',()=>reject(safeError('enrollment-response-unconfirmed')));if(body)req.end(body);else req.end();});}

/** Runtime downloads carry no credentials. Redirects stay HTTPS and are bounded. */
export async function downloadRuntime(descriptor,path,redirects=0,url=descriptor.url){
 if(redirects>4)throw safeError('runtime-download-refused');const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password)throw safeError('runtime-download-refused');
 await new Promise((resolve,reject)=>{const req=request(u,{rejectUnauthorized:true,timeout:30000},async response=>{
  try{if([301,302,303,307,308].includes(response.statusCode)){response.resume();await downloadRuntime(descriptor,path,redirects+1,new URL(response.headers.location,u));return resolve();}
   if(response.statusCode!==200){response.resume();throw safeError('runtime-download-refused');}
   const h=await fs.open(path,'wx',0o600);let size=0;const digest=createHash('sha256');try{for await(const chunk of response){size+=chunk.length;if(size>descriptor.bytes)throw safeError('runtime-download-bound-exceeded');digest.update(chunk);await h.writeFile(chunk);}await h.sync();}finally{await h.close();}
   if(size!==descriptor.bytes||digest.digest('hex')!==descriptor.sha256)throw safeError('runtime-digest-mismatch');resolve();
  }catch{response.destroy();reject(safeError('runtime-download-refused'));}
 });const deadline=setTimeout(()=>req.destroy(),600000);req.once('close',()=>clearTimeout(deadline));req.on('timeout',()=>req.destroy());req.on('error',()=>reject(safeError('runtime-download-refused')));req.end();});
}

/** Stream a verified tar; never extract through a link or outside the selected root. */
export async function extractRuntime(archive,destination,archiveRoot){
 await privateDirectory(destination);destination=await fs.realpath(destination);const links=[],seen=new Set();let pending=Buffer.alloc(0),entry=null,pax={},longName,entries=0,total=0,ended=false;
 const field=(b,start,size)=>b.subarray(start,start+size).toString('utf8').split('\0')[0];
 const number=(b,start,size)=>{const s=field(b,start,size).trim();if(!/^[0-7]*$/.test(s))throw safeError('archive-format-refused');return parseInt(s||'0',8);};
 const member=(name)=>{if(name.startsWith('./'))name=name.slice(2);if(name.endsWith('/'))name=name.slice(0,-1);const parts=name.split('/');if(parts[0]!==archiveRoot||parts.some(p=>!p||p==='.'||p==='..')||name.includes('\0')||name.includes('\\'))throw safeError('archive-path-refused');return {name,path:join(destination,...parts.slice(1))};};
 async function directory(path){const rel=relative(destination,path);if(rel.startsWith('..')||isAbsolute(rel))throw safeError('archive-path-refused');let current=destination;for(const part of rel.split('/').filter(Boolean)){current=join(current,part);try{await fs.mkdir(current,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}const s=await fs.lstat(current);if(!s.isDirectory()||s.isSymbolicLink())throw safeError('archive-path-refused');}}
 async function begin(header){if(header.every(b=>b===0)){ended=true;return;}if(ended)throw safeError('archive-trailing-data-refused');let sum=0;for(let i=0;i<512;i++)sum+=i>=148&&i<156?32:header[i];if(number(header,148,8)!==sum)throw safeError('archive-checksum-refused');
  const type=field(header,156,1)||'0',size=number(header,124,12);if(!Number.isSafeInteger(size)||size>512*1024*1024||++entries>100000||(total+=size)>2*1024*1024*1024)throw safeError('archive-bound-exceeded');
  entry={type,size,left:size,padding:(512-size%512)%512,chunks:[]};if(['x','g','L','K'].includes(type)){if(size>1024*1024)throw safeError('archive-metadata-bound');return;}
  const prefix=field(header,345,155),name=pax.path??longName??(prefix?prefix+'/':'')+field(header,0,100),link=pax.linkpath??field(header,157,100);longName=undefined;pax={};const m=member(name);entry.path=m.path;
  if(seen.has(m.name)&&type!=='5')throw safeError('archive-duplicate-refused');seen.add(m.name);await directory(dirname(m.path)===dirname(destination)?destination:dirname(m.path));
  if(type==='5'){await directory(m.path);if(size)throw safeError('archive-format-refused');}
  else if(type==='0'){entry.handle=await fs.open(m.path,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,number(header,100,8)&0o111?0o700:0o600);}
  else if(type==='1'||type==='2'){if(size||!link||link.includes('\0')||link.includes('\\')||posix.isAbsolute(link))throw safeError('archive-link-refused');const target=type==='1'?posix.normalize(link):posix.normalize(posix.join(posix.dirname(m.name),link));const bound=member(target);links.push({type,path:m.path,target:bound.path,value:link});}
  else throw safeError('archive-type-refused');
 }
 async function finish(){if(entry.handle){await entry.handle.close();entry.handle=null;}if(['x','g'].includes(entry.type)){const bytes=Buffer.concat(entry.chunks);let offset=0;const fields={};while(offset<bytes.length){const end=bytes.indexOf(32,offset);if(end<0)throw safeError('archive-pax-refused');const n=Number(bytes.subarray(offset,end).toString());if(!Number.isSafeInteger(n)||n<4||offset+n>bytes.length||bytes[offset+n-1]!==10)throw safeError('archive-pax-refused');const value=bytes.subarray(end+1,offset+n-1).toString();const equals=value.indexOf('=');if(equals<1)throw safeError('archive-pax-refused');fields[value.slice(0,equals)]=value.slice(equals+1);offset+=n;}if(entry.type==='g'&&(fields.path||fields.linkpath||fields.size))throw safeError('archive-global-path-refused');if(fields.size!==undefined&& !/^\d+$/.test(fields.size))throw safeError('archive-pax-refused');if(entry.type==='x'){if(fields.size!==undefined)throw safeError('archive-pax-size-unsupported');pax=fields;}}
  if(entry.type==='L')longName=Buffer.concat(entry.chunks).toString().replace(/\0.*$/s,'');if(entry.type==='K')pax.linkpath=Buffer.concat(entry.chunks).toString().replace(/\0.*$/s,'');entry=null;
 }
 let expanded=0;const input=createReadStream(archive).pipe(createGunzip());try{for await(const chunk of input){if((expanded+=chunk.length)>2*1024*1024*1024)throw safeError('archive-expanded-bound-exceeded');pending=Buffer.concat([pending,chunk]);while(true){if(!entry){if(pending.length<512)break;await begin(pending.subarray(0,512));pending=pending.subarray(512);if(!entry)continue;}
   if(entry.left){if(!pending.length)break;const length=Math.min(entry.left,pending.length),data=pending.subarray(0,length);if(entry.handle)await entry.handle.writeFile(data);else if(['x','g','L','K'].includes(entry.type))entry.chunks.push(Buffer.from(data));entry.left-=length;pending=pending.subarray(length);if(entry.left)break;}
   if(pending.length<entry.padding)break;pending=pending.subarray(entry.padding);await finish();}}
  if(entry||pending.some(b=>b!==0)||!ended)throw safeError('archive-truncated');
  for(const link of links){await directory(dirname(link.path));if(link.type==='2')await fs.symlink(link.value,link.path);else{const real=await fs.realpath(link.target);if(!inside(destination,real)||(await fs.stat(real)).isDirectory())throw safeError('archive-link-refused');await fs.link(real,link.path);}}
 }finally{input.destroy();if(entry?.handle)await entry.handle.close();}
}

function verifyRegistration(value,p,redemptionId){const keys=['version','status','preparationId','redemptionId','artifactId','origin','deviceId','token','name','createdAt'];if(!value||Object.keys(value).sort().join()!==keys.sort().join()||value.version!==1||value.status!=='registered'||value.preparationId!==p.preparationId||value.redemptionId!==redemptionId||value.artifactId!==p.artifactId||value.origin!==p.origin||value.name!==p.name||!Number.isSafeInteger(value.createdAt)||typeof value.deviceId!=='string'||!/^[A-Za-z0-9._:-]{1,160}$/.test(value.deviceId)||typeof value.token!=='string'||value.token.length<32||value.token.length>4096||/[\x00-\x20\x7f]/.test(value.token))throw safeError('enrollment-response-unconfirmed');return value;}

const probe=`import importlib.metadata,importlib.util,json,os,pathlib,sys
assert tuple(sys.version_info[:2])>=(3,11)
assert importlib.metadata.version('amplifier-unified-client-tui')==sys.argv[1]
assert not any(importlib.util.find_spec(m) for m in ('amplifier_core','amplifier_foundation','amplifier_app_cli'))
import amplifier_tui
p=pathlib.Path(amplifier_tui.__file__).parent
assert (p/'_ahp/bridge.mjs').is_file()
from amplifier_tui.launcher import executable
assert os.access(executable(),os.X_OK)
print(json.dumps({'python':'.'.join(map(str,sys.version_info[:3])),'version':importlib.metadata.version('amplifier-unified-client-tui')}))`;

export async function installTerminal(p,{work,root=join(homedir(),'.local/share/amplifier-unified-terminal'),nodeExecutable=process.execPath}={}){
 if(p?.version!==1||!Number.isSafeInteger(p.expiresAt)||p.expiresAt<=Date.now()/1000)throw safeError('setup-expired');
 if(!/^[A-Za-z0-9._:-]{1,160}$/.test(p.preparationId??'')||!p.artifact||!p.runtimes)throw safeError('invalid-private-profile');
 await platformCheck(p);
 root=resolve(root);await privateDirectory(root);root=await fs.realpath(root);const marker=join(root,'installation.json'), markerBytes=JSON.stringify({format:'amplifier-unified-terminal.installation',version:1});try{const contents=await fs.readdir(root);if(contents.length===0)await exclusive(marker,markerBytes);else if(await fs.readFile(marker,'utf8')!==markerBytes)throw safeError('unrecognized-installation-root');}catch{throw safeError('unrecognized-installation-root');}for(const name of ['versions','preparations','connections'])await privateDirectory(join(root,name));
 const preparation=join(root,'preparations',p.preparationId+'.json'),redemptionId=randomUUID(),candidate=join(root,'versions','client-'+randomUUID());
 let state={version:1,preparationId:p.preparationId,artifactId:p.artifactId,origin:p.origin,redemptionId,phase:'staging',candidate},admitted=false;
 try{await exclusive(preparation,JSON.stringify(state));}catch(e){if(e.code==='EEXIST')throw safeError('original-setup-attempt-already-recorded');throw e;}
 const save=async patch=>{state={...state,...patch};await atomic(preparation,JSON.stringify(state));};
 try{
  await privateDirectory(candidate);await privateDirectory(join(candidate,'bin'));
  if(await hashFile(nodeExecutable)!==p.runtimes.node.executableSha256)throw safeError('node-executable-mismatch');
  await fs.copyFile(nodeExecutable,join(candidate,'bin/node'),constants.COPYFILE_EXCL);await fs.chmod(join(candidate,'bin/node'),0o700);
  const node=join(candidate,'bin/node');if(await run(node,['-p','process.versions.node'])!==p.runtimes.node.version)throw safeError('node-version-mismatch');
  const archive=join(work,'python.tar.gz');await downloadRuntime(p.runtimes.python,archive);await extractRuntime(archive,join(candidate,'python'),p.runtimes.python.archiveRoot);
  const python=join(candidate,'python',p.runtimes.python.executable),real=await fs.realpath(python);if(!inside(join(candidate,'python'),real))throw safeError('python-origin-mismatch');
  if(await run(python,['-I','-B','-c','import sys;print(".".join(map(str,sys.version_info[:3])))'])!==p.runtimes.python.version)throw safeError('python-version-mismatch');
  const wheel=join(candidate,p.artifact.filename);await fs.copyFile(join(work,'client.whl'),wheel,constants.COPYFILE_EXCL);if((await fs.stat(wheel)).size!==p.artifact.bytes||await hashFile(wheel)!==p.artifact.sha256)throw safeError('wheel-digest-mismatch');
  // Dependencies are forbidden: this client is presentation-only and the wheel is self-contained.
  await run(python,['-I','-B','-c','import sys,zipfile,email; z=zipfile.ZipFile(sys.argv[1]); n=[n for n in z.namelist() if n.endswith(".dist-info/METADATA")]; assert len(n)==1; m=email.message_from_bytes(z.read(n[0])); assert m["Name"]=="amplifier-unified-client-tui" and m["Version"]==sys.argv[2] and m["Requires-Python"]==">=3.11" and not m.get_all("Requires-Dist")',wheel,p.artifact.package.version]);
  await run(python,['-I','-B','-m','venv',join(candidate,'environment')]);const executable=join(candidate,'environment/bin/python');
  await run(executable,['-I','-B','-m','pip','install','--no-index','--no-deps','--disable-pip-version-check',wheel],{PIP_CONFIG_FILE:'/dev/null'});
  await run(executable,['-I','-B','-c',probe,p.artifact.package.version]);const helper=await run(executable,['-I','-B','-c','import amplifier_tui,pathlib;print(pathlib.Path(amplifier_tui.__file__).parent/"_ahp/bridge.mjs")']);await run(node,['--check',helper]);
  await run(executable,['-I','-B','-m','amplifier_tui.connected','--help'],{AMPLIFIER_TUI_NODE:node});
  if(p.expiresAt<=Date.now()/1000)throw safeError('setup-expired');
  await save({phase:'enrollment-unknown',validated:true});admitted=true;
  const response=await requestBytes(p.origin+'/setup/terminal/redeem',{body:Buffer.from(JSON.stringify({preparationId:p.preparationId,grant:p.grant,redemptionId,artifactId:p.artifactId})),ca:p.caPem});
  if(response.status!==201){if([403,409,410].includes(response.status)){await save({phase:response.status===409?'consumed-without-credential':'enrollment-refused'});throw safeError(response.status===409?'setup-already-consumed':'enrollment-refused');}throw safeError('enrollment-response-unconfirmed');}
  let data;try{data=JSON.parse(response.bytes);}catch{throw safeError('enrollment-response-unconfirmed');}const registration=verifyRegistration(data,p,redemptionId);
  const connection=join(root,'connections',registration.deviceId);await fs.mkdir(connection,{mode:0o700});
  await exclusive(join(connection,'token'),registration.token+'\n');if(p.caPem)await exclusive(join(connection,'ca.pem'),p.caPem);
  const args=['-I','-B','-m','amplifier_tui.connected','--server',p.origin.replace(/^https:/,'wss:')+'/ahp','--token-file',join(connection,'token'),...(p.caPem?['--ca-file',join(connection,'ca.pem')]:[])];
  const launcher='#!/bin/sh\nset -eu\nfor argument in "$@"; do\n case "$argument" in\n --session|--session=*|--resume|--resume=*|--workspace|--workspace=*|--client|--client=*|--state-dir|--state-dir=*|--agent|--agent=*|--bundle|--bundle=*|--provider|--provider=*|--model|--model=*|--effort|--effort=*|--new|--managed|--list-sessions|--version|--help|-h) ;;\n -*) echo "Prepare a separate connection to change credentials or server." >&2; exit 2;;\n esac\ndone\nunset AMPLIFIER_UNIFIED_TOKEN AMPLIFIER_UNIFIED_URL NODE_OPTIONS NODE_PATH NODE_TLS_REJECT_UNAUTHORIZED NODE_EXTRA_CA_CERTS\nexport AMPLIFIER_TUI_NODE='+q(node)+'\nexec '+[executable,...args].map(q).join(' ')+' "$@"\n';
  await exclusive(join(connection,'launch'),launcher);await fs.chmod(join(connection,'launch'),0o700);
  const record={version:1,id:registration.deviceId,name:p.name,origin:p.origin,artifactId:p.artifactId,package:p.artifact.package,source:p.artifact.source,candidate,launch:join(connection,'launch')};await exclusive(join(connection,'connection.json'),JSON.stringify(record));
  // The reusable launcher reads only this owned default; never overwrite an unrelated command.
  const command=join(root,'launch'),commandOwner=join(root,'launcher-owner.json');const dispatcher='#!/bin/sh\nexec '+q(node)+' '+q(join(root,'launch.mjs'))+' "$@"\n';
  const dispatchJS='import{readFileSync}from"node:fs";import{spawn}from"node:child_process";const r=JSON.parse(readFileSync('+JSON.stringify(join(root,'default.json'))+',"utf8"));const c=spawn(r.launch,process.argv.slice(2),{stdio:"inherit"});c.on("error",()=>process.exit(1));c.on("exit",(code,signal)=>{if(signal)process.kill(process.pid,signal);else process.exit(code??1)});\n';
  let reusable=false;try{await exclusive(join(root,'launch.mjs'),dispatchJS);await exclusive(command,dispatcher);await fs.chmod(command,0o700);await exclusive(commandOwner,JSON.stringify({version:1,commandSha256:sha(dispatcher),helperSha256:sha(dispatchJS)}));reusable=true;}catch(e){if(e.code!=='EEXIST')throw e;try{const owned=JSON.parse(await fs.readFile(commandOwner,'utf8'));reusable=owned.version===1&&owned.commandSha256===await hashFile(command)&&owned.helperSha256===await hashFile(join(root,'launch.mjs'));}catch{}}
  await atomic(join(root,'default.json'),JSON.stringify({version:1,id:registration.deviceId,launch:record.launch}));await save({phase:'installed',deviceId:registration.deviceId,connection,launch:record.launch});
  return {version:1,status:'installed',deviceId:registration.deviceId,launch:reusable?command:record.launch,connected:false};
 }catch(error){if(!admitted){await save({phase:'staging-refused',reason:error.code??'local-staging-failed'});await fs.rm(candidate,{recursive:true,force:true});}else if(state.phase==='enrollment-unknown')await save({reason:'original-enrollment-unconfirmed-no-replay'});throw safeError(error.code??'installation-incomplete');}
}

if(process.argv[1]&&await fs.realpath(process.argv[1]).catch(()=>null)===fileURLToPath(import.meta.url)){
 try{const profile=JSON.parse(await fs.readFile(process.argv[2],'utf8'));const result=await installTerminal(profile,{work:process.argv[3],root:process.env.AMPLIFIER_TERMINAL_HOME,nodeExecutable:join(process.argv[3],'node')});console.log('Installed locally. Run: '+result.launch);console.log('A live connection is verified when the terminal opens. Existing conversations remain on the host.');}
 catch(error){console.error('Terminal setup did not finish ('+(error.code??'local-setup-failed')+'). Existing connections are preserved. If registration may have occurred, inspect this setup on the service; do not replay it.');process.exitCode=1;}
}
