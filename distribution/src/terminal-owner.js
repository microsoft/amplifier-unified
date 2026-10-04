import {DatabaseSync} from 'node:sqlite';
import {constants,openSync,closeSync,fstatSync,readFileSync,writeFileSync,renameSync,fsyncSync,mkdirSync,chmodSync,unlinkSync} from 'node:fs';
import {join} from 'node:path';
import {randomBytes,randomUUID,createHash,timingSafeEqual} from 'node:crypto';
import {FacadeFence} from './facade-fence.js';
import {loadTerminalArtifact,readTerminalWheel,parseTerminalJSON} from './terminal-artifacts.js';

const TTL=1800,MAX_SCRIPT=96*1024*1024;
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const sha=value=>createHash('sha256').update(value).digest('hex');
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value);
const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const command=value=>typeof value==='string'&&value.length>0&&value.length<=256&&!/[\x00-\x1f]/.test(value);
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).every(key=>keys.includes(key));
const text={type:'string',minLength:1,maxLength:128};
const define=(description,properties,required=[])=>({description,schema:{type:'object',additionalProperties:false,properties,required}});
const schemas={
 'terminal.prepare':define('Prepare a private one-use Terminal installer from configured qualified artifacts. Registration does not prove installation or connectivity.',{platform:text,name:{type:'string',minLength:1,maxLength:80},artifactId:text},['platform','name']),
 'terminal.devices':define('List registered Terminal devices without credentials. Registration does not prove that a device is online.',{cursor:{type:'string',maxLength:256},limit:{type:'integer',minimum:1,maximum:50}}),
 'terminal.revoke':define('Revoke one Terminal credential and disconnect only its sockets. Already admitted work continues.',{deviceId:{type:'string',format:'uuid'}},['deviceId']),
 'terminal.receipt':define('Read the original exact Terminal command receipt without repeating its effect.',{commandId:{type:'string',minLength:1,maxLength:256}},['commandId']),
};

function readRegular(path,limit){
 const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try{const before=fstatSync(fd);if(!before.isFile()||before.size>limit)throw Error('Configured Terminal artifact is not a bounded regular file');const bytes=readFileSync(fd),after=fstatSync(fd);if(bytes.length!==before.size||before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ctimeMs!==after.ctimeMs)throw Error('Terminal artifact changed while reading');return bytes;}finally{closeSync(fd);}
}
function writePrivate(path,bytes){
 const temp=path+'.'+randomUUID()+'.tmp';let fd;
 try{fd=openSync(temp,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY,0o600);writeFileSync(fd,bytes);fsyncSync(fd);closeSync(fd);fd=undefined;renameSync(temp,path);const parent=openSync(join(path,'..'),constants.O_RDONLY);try{fsyncSync(parent);}finally{closeSync(parent);}}
 catch(error){if(fd!==undefined)closeSync(fd);try{unlinkSync(temp);}catch{}throw error;}
}
const publicDevice=row=>({deviceId:row.id,preparationId:row.preparation,name:row.name,artifactId:row.artifact,platform:row.platform,createdAt:row.created,revokedAt:row.revoked??null});

/** Single configured account. The capability host supplies that identity; no client argument can select it. */
export function createTerminalOwner({directory,account,origin,artifacts,renderInstaller,onInvalidate=()=>{},onMayBeIdle=()=>{},now=()=>Math.floor(Date.now()/1000)}){
 if(typeof account!=='string'||!account||account.length>512)throw Error('Terminal requires a configured account');
 const url=new URL(origin);if(url.protocol!=='https:'||url.origin!==origin||url.username||url.password)throw Error('Terminal requires an exact configured HTTPS origin');
 if(!Array.isArray(artifacts)||!artifacts.length||artifacts.length>32||typeof renderInstaller!=='function')throw Error('Terminal requires a qualified configured artifact feed and installer renderer');
 const feed=new Map();
 for(const entry of artifacts){
  if(!identifier(entry.id)||!identifier(entry.platform)||feed.has(entry.id))throw Error('Invalid configured Terminal artifact');
  // A missing release feed must not make existing device revocation or original
  // receipts inaccessible. It disables only new preparations, without fallback.
  let copy;try{copy=loadTerminalArtifact(entry);}catch{copy={id:entry.id,platform:entry.platform,unavailable:true};}feed.set(copy.id,copy);
 }
 mkdirSync(directory,{recursive:true,mode:0o700});chmodSync(directory,0o700);
 const intake=new FacadeFence({directory:join(directory,'intake'),id:'terminal',serviceStop:true,retentionHide:true,managedFiles:true,onMayBeIdle});
 let db;
 try{
  db=new DatabaseSync(join(directory,'terminal.sqlite3'));chmodSync(join(directory,'terminal.sqlite3'),0o600);
  db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY CHECK(id=1),account TEXT NOT NULL,origin TEXT NOT NULL); CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT NOT NULL,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS preparations(id TEXT PRIMARY KEY,command TEXT UNIQUE NOT NULL,artifact TEXT NOT NULL,platform TEXT NOT NULL,name TEXT NOT NULL,expires INTEGER NOT NULL,grant_hash TEXT NOT NULL,script_hash TEXT NOT NULL,script_bytes INTEGER NOT NULL,redemption TEXT,device TEXT); CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,preparation TEXT UNIQUE NOT NULL,name TEXT NOT NULL,artifact TEXT NOT NULL,platform TEXT NOT NULL,token_hash TEXT NOT NULL,created INTEGER NOT NULL,revoked INTEGER); CREATE INDEX IF NOT EXISTS device_page ON devices(created DESC,id DESC)');
  const prior=db.prepare('SELECT * FROM binding WHERE id=1').get();if(prior&&(prior.account!==account||prior.origin!==origin))throw Error('Terminal owner account or origin changed');
  if(!prior)db.prepare('INSERT INTO binding VALUES(1,?,?)').run(account,origin);
  db.exec('CREATE TABLE IF NOT EXISTS artifact_bindings(id TEXT PRIMARY KEY,signature TEXT NOT NULL)');
  db.exec('CREATE INDEX IF NOT EXISTS pending_preparation_expiry ON preparations(expires) WHERE device IS NULL; CREATE INDEX IF NOT EXISTS active_device ON devices(id) WHERE revoked IS NULL');
  for(const entry of feed.values())if(!entry.unavailable){
   const signature=sha(canonical({platform:entry.platform,manifestSha256:entry.artifact.manifestSha256,filename:entry.artifact.filename,runtimes:entry.runtimes})),priorArtifact=db.prepare('SELECT signature FROM artifact_bindings WHERE id=?').get(entry.id);
   if(priorArtifact&&priorArtifact.signature!==signature){entry.unavailable=true;continue;}
   if(!priorArtifact)db.prepare('INSERT INTO artifact_bindings VALUES(?,?)').run(entry.id,signature);
  }
 }catch(error){db?.close();intake.close();throw error;}
 const downloads=join(directory,'downloads');mkdirSync(downloads,{recursive:true,mode:0o700});chmodSync(downloads,0o700);
 const sockets=new Map(),pending=new Map();let closed=false,revision=0,downloadBytes=0;
 const changed=()=>{revision++;try{onInvalidate('terminal','host');}catch{}};
 const transact=work=>{db.exec('BEGIN IMMEDIATE');try{const result=work();db.exec('COMMIT');return result;}catch(error){db.exec('ROLLBACK');throw error;}};
 const receipt=id=>{if(!command(id))throw Error('Exact Terminal command ID required');const row=db.prepare('SELECT body FROM commands WHERE id=?').get(id);return row?JSON.parse(row.body):null;};
 const save=(id,signature,body)=>db.prepare('INSERT INTO commands VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body').run(id,signature,canonical(body));
 const start=(id,operation,args)=>{
  if(!command(id))throw Error('Bounded original Terminal command ID required');const signature=sha(canonical({account,origin,operation,args})),prior=db.prepare('SELECT signature,body FROM commands WHERE id=?').get(id);
  if(prior){if(prior.signature!==signature)throw Error('Terminal command ID is bound to different arguments');return {signature,prior:JSON.parse(prior.body)};}
  return {signature};
 };
 const refusal=(id,operation,signature,reason)=>{const body={commandId:id,operation,status:'failed',executed:false,error:{code:reason},replayed:false};save(id,signature,body);return body;};
 async function prepare(id,args){
  if(!exact(args,['platform','name','artifactId'])||!identifier(args.platform)||typeof args.name!=='string'||!args.name.trim()||args.name.length>80||/[\x00-\x1f\x7f]/.test(args.name)||(args.artifactId!==undefined&&!identifier(args.artifactId)))throw Error('Invalid Terminal preparation');
  const state=start(id,'terminal.prepare',args);if(state.prior)return state.prior;
  const selected=args.artifactId?feed.get(args.artifactId):[...feed.values()].filter(item=>item.platform===args.platform).at(0);
  if(!selected||selected.platform!==args.platform)return refusal(id,'terminal.prepare',state.signature,'terminal-platform-unavailable');
  if(selected.unavailable)return refusal(id,'terminal.prepare',state.signature,'terminal-artifact-unavailable');
  if(pending.size>=2||db.prepare('SELECT count(*) AS n FROM preparations WHERE device IS NULL AND expires>?').get(now()).n+pending.size>=20)return refusal(id,'terminal.prepare',state.signature,'terminal-preparation-capacity');
  let wheelBytes;try{wheelBytes=readTerminalWheel(selected);}catch{return refusal(id,'terminal.prepare',state.signature,'terminal-artifact-unavailable');}
  // Original command admission is durable before private file generation. A crash
  // leaves this exact command unknown; no repeated renderer or fresh grant.
  const unknown={commandId:id,operation:'terminal.prepare',status:'unknown',replayed:false};save(id,state.signature,unknown);
  const preparationId=randomUUID(),grant=randomBytes(32).toString('base64url'),expiresAt=now()+TTL;
  try{
   const rendered=await renderInstaller({preparation:{preparationId,grant,origin,expiresAt,artifactId:selected.id,name:args.name},artifact:{release:structuredClone(selected.artifact.release),filename:selected.artifact.filename,wheelBytes},runtimes:structuredClone(selected.runtimes)});
   if(!Buffer.isBuffer(rendered?.bytes)||rendered.bytes.length>MAX_SCRIPT||rendered.bytes.length===0||rendered.contentType!=='text/x-shellscript; charset=utf-8'||typeof rendered.filename!=='string'||!/^[A-Za-z0-9_.-]{1,128}$/.test(rendered.filename)||rendered.sha256!==sha(rendered.bytes))throw Error('Invalid private Terminal installer');
   writePrivate(join(downloads,preparationId+'.sh'),rendered.bytes);
   const result={version:1,preparationId,artifactId:selected.id,artifact:{manifestSha256:selected.artifact.manifestSha256,wheelSha256:selected.selected.sha256,version:selected.artifact.release.package.version,sourceCommit:selected.artifact.release.source.commit},platform:args.platform,name:args.name,origin,expiresAt,download:{url:origin+'/setup/terminal/download/'+preparationId,filename:rendered.filename}};
   const body={commandId:id,operation:'terminal.prepare',status:'completed',result,replayed:false};
   transact(()=>{db.prepare('INSERT INTO preparations(id,command,artifact,platform,name,expires,grant_hash,script_hash,script_bytes) VALUES(?,?,?,?,?,?,?,?,?)').run(preparationId,id,selected.id,args.platform,args.name,expiresAt,sha(grant),rendered.sha256,rendered.bytes.length);save(id,state.signature,body);});changed();return body;
  }catch{return unknown;}
 }
 function devices(args={}){
  if(!exact(args,['cursor','limit'])||args.limit!==undefined&&(!Number.isInteger(args.limit)||args.limit<1||args.limit>50))throw Error('Invalid Terminal device page');
  let cursor; if(args.cursor!==undefined){if(typeof args.cursor!=='string'||args.cursor.length>256)throw Error('Invalid Terminal cursor');try{cursor=JSON.parse(Buffer.from(args.cursor,'base64url').toString());}catch{throw Error('Invalid Terminal cursor');}if(!exact(cursor,['created','id'])||!Number.isSafeInteger(cursor.created)||!uuid(cursor.id))throw Error('Invalid Terminal cursor');}
  const limit=args.limit??25,rows=cursor?db.prepare('SELECT * FROM devices WHERE created<? OR (created=? AND id<?) ORDER BY created DESC,id DESC LIMIT ?').all(cursor.created,cursor.created,cursor.id,limit+1):db.prepare('SELECT * FROM devices ORDER BY created DESC,id DESC LIMIT ?').all(limit+1),items=rows.slice(0,limit),last=items.at(-1);
  return {items:items.map(publicDevice),nextCursor:rows.length>limit?Buffer.from(JSON.stringify({created:last.created,id:last.id})).toString('base64url'):null};
 }
 function revoke(id,args){
  if(!exact(args,['deviceId'])||!uuid(args.deviceId))throw Error('Invalid Terminal device identity');const state=start(id,'terminal.revoke',args);if(state.prior)return state.prior;
  const device=db.prepare('SELECT * FROM devices WHERE id=?').get(args.deviceId);if(!device)return refusal(id,'terminal.revoke',state.signature,'terminal-device-unavailable');
  const at=device.revoked??now(),body={commandId:id,operation:'terminal.revoke',status:'completed',result:{deviceId:device.id,revokedAt:at,admittedWork:'continues'},replayed:false};
  transact(()=>{db.prepare('UPDATE devices SET revoked=? WHERE id=?').run(at,device.id);save(id,state.signature,body);});
  for(const connection of sockets.get(device.id)??[])try{connection.stop();}catch{}changed();return body;
 }
 function redeem(args){
  if(!exact(args,['preparationId','grant','redemptionId','artifactId'])||!uuid(args.preparationId)||!uuid(args.redemptionId)||!identifier(args.artifactId)||typeof args.grant!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(args.grant))return {code:403,body:{version:1,status:'refused'}};
  return transact(()=>{
   const row=db.prepare('SELECT * FROM preparations WHERE id=?').get(args.preparationId);
   if(!row||row.artifact!==args.artifactId||!timingSafeEqual(Buffer.from(row.grant_hash,'hex'),Buffer.from(sha(args.grant),'hex')))return {code:403,body:{version:1,status:'refused'}};
   if(row.redemption){const device=db.prepare('SELECT * FROM devices WHERE id=?').get(row.device);return {code:409,body:{version:1,status:'consumed',preparationId:row.id,artifactId:row.artifact,origin,credentialAvailable:false,...(row.redemption===args.redemptionId?{redemptionId:row.redemption,deviceId:row.device,createdAt:device.created}:{})}};}
   if(row.expires<=now())return {code:410,body:{version:1,status:'expired'}};
   if(db.prepare('SELECT count(*) AS n FROM devices WHERE revoked IS NULL').get().n>=100)return {code:409,body:{version:1,status:'capacity'}};
   const deviceId=randomUUID(),token='amt_'+deviceId+'.'+randomBytes(48).toString('base64url'),createdAt=now();
   db.prepare('INSERT INTO devices VALUES(?,?,?,?,?,?,?,NULL)').run(deviceId,row.id,row.name,row.artifact,row.platform,sha(token),createdAt);
   db.prepare('UPDATE preparations SET redemption=?,device=? WHERE id=?').run(args.redemptionId,deviceId,row.id);changed();
   return {code:201,body:{version:1,status:'registered',preparationId:row.id,redemptionId:args.redemptionId,artifactId:row.artifact,origin,deviceId,token,name:row.name,createdAt}};
  });
 }
 function attachDevice(authorization,stop){
  if(closed||typeof stop!=='function'||typeof authorization!=='string')throw Error('Terminal device unavailable');
  const match=/^Bearer (amt_([a-f0-9-]{36})\.[A-Za-z0-9_-]{64})$/.exec(authorization);if(!match||!uuid(match[2]))throw Error('Terminal device unavailable');
  const leave=intake.enter(),row=db.prepare('SELECT token_hash,revoked FROM devices WHERE id=?').get(match[2]);
  if(!row||row.revoked!==null||!timingSafeEqual(Buffer.from(row.token_hash,'hex'),Buffer.from(sha(match[1]),'hex'))){leave();throw Error('Terminal device unavailable');}
  // Synchronous registration and revocation share the owner event loop. There is
  // no await between token validation and attaching the exact socket lifetime.
  const member={stop};const set=sockets.get(match[2])??new Set();set.add(member);sockets.set(match[2],set);let released=false;
  return {account,deviceId:match[2],release(){if(released)return;released=true;set.delete(member);if(!set.size)sockets.delete(match[2]);leave();}};
 }
 function authorize(context){if(closed||context?.account!==account)throw Error('Terminal account authority required');}
 const snapshot=()=>{const available=[...feed.values()].filter(item=>!item.unavailable);return {topic:'terminal',scope:'host',revision,data:{terminal:{available:available.length>0,origin,grantTtlSeconds:TTL,platforms:[...new Set(available.map(item=>item.platform))],artifacts:available.map(({id,platform})=>({artifactId:id,platform})),registrationIsNotReadiness:true}}};};
 async function handle(req,res,context){
  const done=intake.enter();let released=false,reserved=0;const leave=()=>{if(!released){released=true;downloadBytes-=reserved;done();}};res.once('close',leave);
  try{
   res.setHeader('Cache-Control','no-store');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('X-Content-Type-Options','nosniff');
   if(req.headers.host!==url.host||req.headers.origin&&req.headers.origin!==origin)throw Error('Terminal request authority mismatch');
   if(req.url==='/setup/terminal/redeem'&&req.method==='POST'){
    if(req.headers['content-type']!=='application/json'||Number(req.headers['content-length']??0)>4096)throw Error('Invalid Terminal redemption');const chunks=[];let size=0;
    for await(const chunk of req){size+=chunk.length;if(size>4096)throw Error('Terminal redemption exceeds bound');chunks.push(chunk);}
    const result=redeem(parseTerminalJSON(Buffer.concat(chunks)));res.writeHead(result.code,{'Content-Type':'application/json'});res.end(JSON.stringify(result.body));return;
   }
   authorize(context);const match=/^\/setup\/terminal\/download\/([a-f0-9-]{36})$/.exec(req.url);
   if(!match||req.method!=='GET'||!uuid(match[1]))throw Error('Terminal download unavailable');
   const row=db.prepare('SELECT * FROM preparations WHERE id=?').get(match[1]);if(!row||row.expires<=now()||row.redemption){res.writeHead(410);res.end('This setup file is expired or already used.');return;}
   if(downloadBytes+row.script_bytes>MAX_SCRIPT){res.writeHead(429);res.end('Terminal download capacity reached.');return;}reserved=row.script_bytes;downloadBytes+=reserved;
   const body=receipt(row.command),bytes=readRegular(join(downloads,row.id+'.sh'),MAX_SCRIPT);if(bytes.length!==row.script_bytes||sha(bytes)!==row.script_hash)throw Error('Private Terminal installer changed');
   res.writeHead(200,{'Content-Type':'text/x-shellscript; charset=utf-8','Content-Length':bytes.length,'Content-Disposition':'attachment; filename="'+body.result.download.filename+'"'});res.end(bytes);
  }catch{if(!res.headersSent)res.writeHead(403,{'Content-Type':'text/plain'});res.end('Terminal setup request refused.');}
 }
 const owner={
  manifest:{version:1,topics:{terminal:{version:1,uri:'amplifier-capability://terminal',scope:'host',watch:true}},actions:Object.fromEntries(Object.keys(schemas).map(operation=>[operation,{topic:'terminal',operation,method:'x-amplifier/capabilityAction'}]))},
  quiescenceParticipant:intake.participant,quiescenceAccess:{'terminal.devices':'read','terminal.receipt':'read'},actionSchemas:()=>schemas,
  async read(request,context){authorize(context);const target=new URL(request.uri);target.search='';if(request.topic!=='terminal'||request.scope!=='host'||target.href!=='amplifier-capability://terminal')throw Error('Terminal host scope required');return intake.run(true,()=>snapshot());},
  async action(request,context){
   authorize(context);if(request.version!==1||request.topic!=='terminal'||!['host','ahp-root://'].includes(request.channel)||!Object.hasOwn(schemas,request.operation))throw Error('Unadvertised Terminal action');
   const passive=Boolean(owner.quiescenceAccess[request.operation]);return intake.run(passive,async()=>{
    let result;const args=request.args??{};
    if(request.operation==='terminal.devices')result=devices(args);
    else if(request.operation==='terminal.receipt'){if(!exact(args,['commandId']))throw Error('Exact Terminal receipt required');result={receipt:receipt(args.commandId),replayed:false};}
    else if(request.operation==='terminal.revoke')result={receipt:revoke(request.commandId,args)};
    else {const existing=pending.get(request.commandId);if(existing){start(request.commandId,request.operation,args);result={receipt:await existing};}else{const task=prepare(request.commandId,args);pending.set(request.commandId,task);try{result={receipt:await task};}finally{pending.delete(request.commandId);}}}
    if(result.receipt?.status==='unknown'&&!passive)throw Object.assign(Error('Terminal setup outcome is unknown. Check the original receipt; do not repeat registration.'),{data:{receipt:request.commandId,outcome:'unknown'}});
    return {accepted:result.receipt?.status!=='failed',result,updates:[]};
   });
  },
  // Capability receipts never contain secrets. These ports are trusted composition
  // interfaces, not callable actions. Grant redemption is authenticated by its body.
  terminalAccess:{origin,attachDevice,handleRedemption:(req,res)=>handle(req,res,undefined)},
  httpHandlers:[{matches:path=>path.startsWith('/setup/terminal/download/'),handle}],
  async close(){if(closed)return;closed=true;for(const set of sockets.values())for(const member of set)try{member.stop();}catch{}await intake.drain();db.close();intake.close();},
 };
 return owner;
}
