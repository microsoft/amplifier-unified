import {constants,openSync,closeSync,fstatSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const same=(a,b)=>JSON.stringify(order(a))===JSON.stringify(order(b));
const order=value=>Array.isArray(value)?value.map(order):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).sort().map(([k,v])=>[k,order(v)])):value;
const fields=(v,keys)=>{if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join(',')!==[...keys].sort().join(','))throw Error('Unexpected Terminal release fields');};
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const asset=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$/.test(v);
const compat={ahpVersions:['0.9.0'],sdkVersion:'0.9.0',pythonRequires:'>=3.11',nodeRequires:'>=22'};
const checks=['protocol-install','protocol-build','native-build','native-tests','ruff','direction','current-tests','wheel-build','install-env','install-wheel','installed-isolation','installed-terminal'];
function version(value,major,minor){if(typeof value!=='string'||!/^\d+\.\d+\.\d+$/.test(value))throw Error('Exact qualified runtime version required');const [a,b]=value.split('.').map(Number);if(a<major||a===major&&b<minor)throw Error('Terminal runtime version is unsupported');}
export function readTerminalFile(path,limit){
 if(typeof path!=='string'||!path.startsWith('/'))throw Error('Configured absolute Terminal artifact path required');
 const fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try{const before=fstatSync(fd);if(!before.isFile()||before.size>limit)throw Error('Terminal artifact must be bounded and regular');const bytes=readFileSync(fd),after=fstatSync(fd);if(bytes.length!==before.size||before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ctimeMs!==after.ctimeMs)throw Error('Terminal artifact changed');return bytes;}finally{closeSync(fd);}
}
// JSON.parse alone silently accepts duplicate authority fields. This small bounded
// syntax walk rejects duplicate object keys before decoding the already hashed file.
export function parseTerminalJSON(bytes){
 const text=bytes.toString('utf8');let i=0;
 const ws=()=>{while(/\s/.test(text[i]??'')&&i<text.length)i++;};
 const string=()=>{const start=i++;for(;i<text.length;i++){if(text[i]==='\\'){i++;continue;}if(text[i]==='"'){i++;return JSON.parse(text.slice(start,i));}}throw Error('Invalid Terminal JSON');};
 const value=depth=>{if(depth>32)throw Error('Terminal JSON depth');ws();const c=text[i];if(c==='"'){string();return;}if(c==='{'||c==='['){i++;const object=c==='{',end=object?'}':']',seen=new Set();ws();if(text[i]===end){i++;return;}for(;;){ws();if(object){if(text[i]!=='"')throw Error('Invalid Terminal JSON');const key=string();if(seen.has(key))throw Error('Duplicate Terminal JSON key');seen.add(key);ws();if(text[i++]!==':')throw Error('Invalid Terminal JSON');}value(depth+1);ws();if(text[i]===end){i++;return;}if(text[i++]!==',')throw Error('Invalid Terminal JSON');}}else{const match=/^(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/.exec(text.slice(i));if(!match)throw Error('Invalid Terminal JSON');i+=match[0].length;}};
 value(0);ws();if(i!==text.length)throw Error('Invalid Terminal JSON');return JSON.parse(text);
}

/** Validate the TUI owner's v1 contract and exact evidence bytes. No network or callback authority. */
export function loadTerminalArtifact(entry){
 fields(entry,['id','platform','artifact','runtimes']);fields(entry.artifact,['manifestPath','manifestSha256','filename','wheelPath','evidencePath']);
 if(!hex(entry.artifact.manifestSha256))throw Error('Reviewed Terminal manifest digest required');
 const bytes=readTerminalFile(entry.artifact.manifestPath,128*1024);if(sha(bytes)!==entry.artifact.manifestSha256)throw Error('Terminal release authority digest changed');
 const release=parseTerminalJSON(bytes);fields(release,['format','schemaVersion','package','source','compatibility','artifacts']);fields(release.package,['name','version']);fields(release.source,['repository','commit']);
 if(release.format!=='amplifier-unified-client-tui.release'||release.schemaVersion!==1||release.package.name!=='amplifier-unified-client-tui'||!/^\d+\.\d+\.\d+(?:rc\d+)?$/.test(release.package.version)||release.source.repository!=='https://github.com/microsoft/amplifier-unified-client-tui'||!/^[a-f0-9]{40}$/.test(release.source.commit)||!same(release.compatibility,compat)||!Array.isArray(release.artifacts)||!release.artifacts.length||release.artifacts.length>16)throw Error('Unsupported Terminal release');
 const names=new Set(),targets=new Set();let selected;
 for(const item of release.artifacts){
  fields(item,['filename','bytes','sha256','target','qualification']);fields(item.target,['os','arch','wheelTag','osFloor']);
  const m=/^py3-none-(?:linux_(x86_64|aarch64)|macosx_(\d+)_(\d+)_(arm64|x86_64))$/.exec(item.target.wheelTag);
  if(!m)throw Error('Unqualified Terminal target');const target=m[1]?{os:'linux',arch:m[1]==='aarch64'?'arm64':'x86_64',wheelTag:m[0],osFloor:null}:{os:'macos',arch:m[4],wheelTag:m[0],osFloor:m[2]+'.'+m[3]};
  if(!same(item.target,target)||item.filename!==`amplifier_unified_client_tui-${release.package.version}-${target.wheelTag}.whl`||!asset(item.filename)||names.has(item.filename)||targets.has(target.wheelTag)||!Number.isSafeInteger(item.bytes)||item.bytes<1||item.bytes>256*1024*1024||!hex(item.sha256))throw Error('Terminal artifact identity mismatch');names.add(item.filename);targets.add(target.wheelTag);
  const q=item.qualification;fields(q,['status','evidenceFilename','evidenceSha256','installedIsolation','privacy','actualPTY','testedPython','testedNode','testedOS','minimumGlibc']);
  if(q.status!=='qualified'||!['installedIsolation','privacy','actualPTY'].every(k=>q[k]===true)||q.evidenceFilename!==item.filename.slice(0,-4)+'.qualification.json'||!asset(q.evidenceFilename)||!hex(q.evidenceSha256)||typeof q.testedOS!=='string'||!q.testedOS||q.testedOS.length>120||(target.os==='linux'?!/^\d+\.\d+$/.test(q.minimumGlibc):q.minimumGlibc!==null))throw Error('Terminal qualification unavailable');version(q.testedPython,3,11);version(q.testedNode,22,0);
  if(item.filename===entry.artifact.filename)selected=item;
 }
 if(!selected||entry.platform!==selected.target.os+'-'+selected.target.arch||selected.bytes>64*1024*1024)throw Error('Selected Terminal platform unavailable');
 const proofBytes=readTerminalFile(entry.artifact.evidencePath,128*1024);if(sha(proofBytes)!==selected.qualification.evidenceSha256)throw Error('Terminal evidence digest changed');
 const proof=parseTerminalJSON(proofBytes),q=selected.qualification;fields(proof,['format','schemaVersion','package','source','compatibility','wheel','qualification','checks','checkLogHashes','limits']);fields(proof.checkLogHashes,checks);
 if(proof.format!==release.format+'.qualification'||proof.schemaVersion!==1||!same(proof.package,release.package)||!same(proof.source,release.source)||!same(proof.compatibility,compat)||!same(proof.wheel,Object.fromEntries(['filename','bytes','sha256','target'].map(k=>[k,selected[k]])))||!same(proof.qualification,Object.fromEntries(Object.entries(q).filter(([k])=>!['evidenceFilename','evidenceSha256'].includes(k))))||!same(proof.checks,Object.fromEntries(checks.map(k=>[k,'passed'])))||!Object.values(proof.checkLogHashes).every(hex)||!Array.isArray(proof.limits)||proof.limits.length<1||proof.limits.length>8||proof.limits.some(v=>typeof v!=='string'||v.length>200))throw Error('Terminal evidence does not bind selected release');
 fields(entry.runtimes,['python','node']);
 for(const [kind,runtime]of Object.entries(entry.runtimes)){
  fields(runtime,['version','url','bytes','sha256','archive','archiveRoot','executable','target',...(kind==='node'?['executableSha256']:[])]);fields(runtime.target,['os','arch']);
  const endpoint=new URL(runtime.url);version(runtime.version,kind==='python'?3:22,kind==='python'?11:0);
  if(endpoint.protocol!=='https:'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||!Number.isSafeInteger(runtime.bytes)||runtime.bytes<1||runtime.bytes>512*1024*1024||!hex(runtime.sha256)||kind==='node'&&!hex(runtime.executableSha256)||kind==='python'&&!runtime.version.startsWith('3.')||runtime.archive!=='tar.gz'||!asset(runtime.archiveRoot)||typeof runtime.executable!=='string'||!runtime.executable||runtime.executable.length>240||runtime.executable.split('/').some(v=>!asset(v)||v==='.'||v==='..')||!same(runtime.target,{os:selected.target.os,arch:selected.target.arch}))throw Error('Unqualified managed Terminal runtime');
 }
 return {...structuredClone(entry),artifact:{...structuredClone(entry.artifact),release},selected:structuredClone(selected)};
}
export function readTerminalWheel(entry){const bytes=readTerminalFile(entry.artifact.wheelPath,64*1024*1024);if(bytes.length!==entry.selected.bytes||sha(bytes)!==entry.selected.sha256)throw Error('Terminal wheel digest changed');return bytes;}
