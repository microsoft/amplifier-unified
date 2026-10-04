import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';

const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const fail=()=>{throw new Error('Invalid qualified terminal installer input');};
const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
const text=(value,max=256)=>typeof value==='string'&&value.length>0&&value.length<=max&&!/[\x00-\x1f\x7f]/.test(value);
const id=value=>text(value,160)&&/^[A-Za-z0-9._:-]+$/.test(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const relative=value=>text(value)&&/^[A-Za-z0-9._/-]+$/.test(value)&&!value.startsWith('/')&&value.split('/').every(p=>p&&p!=='.'&&p!=='..');
const quote=value=>"'"+value.replaceAll("'","'\\''")+"'";
const encoded=value=>Buffer.from(value).toString('base64');
function https(value,origin=false){
 if(!text(value,2048))fail();let url;try{url=new URL(value);}catch{fail();}
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||origin&&url.origin!==value)fail();return value;
}
function runtime(value,kind,target){
 if(!object(value)||!/^\d+\.\d+\.\d+$/.test(value.version??''))fail();
 const [major,minor]=value.version.split('.').map(Number);
 if(kind==='python'&&(major!==3||minor<11)||kind==='node'&&major<22)fail();
 if(!Number.isSafeInteger(value.bytes)||value.bytes<1||value.bytes>512*1024*1024||!hash(value.sha256)||value.archive!=='tar.gz'||!relative(value.archiveRoot)||value.archiveRoot.includes('/')||!relative(value.executable))fail();
 if(value.target?.os!==target.os||value.target?.arch!==target.arch||kind==='node'&&!hash(value.executableSha256))fail();
 return {version:value.version,url:https(value.url),bytes:value.bytes,sha256:value.sha256,archive:value.archive,archiveRoot:value.archiveRoot,executable:value.executable,target:{os:target.os,arch:target.arch},...(kind==='node'?{executableSha256:value.executableSha256}:{})};
}

/** Pure private-download renderer. Caller owns release qualification/enrollment authority. */
export function renderTerminalInstaller({preparation,artifact,runtimes}){
 const p=preparation,r=artifact?.release;
 if(!object(p)||!id(p.preparationId)||!id(p.artifactId)||!text(p.grant,1024)||!text(p.name,128)||!Number.isSafeInteger(p.expiresAt)||p.expiresAt<1)fail();
 https(p.origin,true);
 if(p.caPem!==undefined&&(typeof p.caPem!=='string'||Buffer.byteLength(p.caPem)>256*1024||p.caPem.includes('PRIVATE KEY')||p.caPem&&!/^-----BEGIN CERTIFICATE-----[\s\S]+-----END CERTIFICATE-----\s*$/.test(p.caPem)))fail();
 if(!object(r)||r.format!=='amplifier-unified-client-tui.release'||r.schemaVersion!==1||r.package?.name!=='amplifier-unified-client-tui'||!text(r.package?.version,80)||!/^[0-9][A-Za-z0-9.!+_-]*$/.test(r.package.version)||!Array.isArray(r.artifacts)||r.artifacts.length<1||r.artifacts.length>16)fail();
 https(r.source?.repository);if(!/^[a-f0-9]{40}$/.test(r.source?.commit??''))fail();
 if(!r.compatibility?.ahpVersions?.includes('0.9.0')||r.compatibility.sdkVersion!=='0.9.0'||r.compatibility.pythonRequires!=='>=3.11'||r.compatibility.nodeRequires!=='>=22')fail();
 const choices=r.artifacts.filter(row=>row.filename===artifact.filename);if(choices.length!==1)fail();const a=choices[0],t=a.target,q=a.qualification;
 if(!text(a.filename,200)||!/^[A-Za-z0-9_.+-]+\.whl$/.test(a.filename)||!a.filename.startsWith('amplifier_unified_client_tui-'+r.package.version+'-')||!hash(a.sha256)||!Number.isSafeInteger(a.bytes)||a.bytes<1||a.bytes>64*1024*1024||!Buffer.isBuffer(artifact.wheelBytes)||artifact.wheelBytes.length!==a.bytes||digest(artifact.wheelBytes)!==a.sha256)fail();
 if(!['macos','linux'].includes(t?.os)||!['arm64','x86_64'].includes(t?.arch)||!text(t.wheelTag,100)||!a.filename.endsWith('-'+t.wheelTag+'.whl')||!(t.osFloor===null||/^\d+\.\d+$/.test(t.osFloor??''))||!text(q?.testedOS,120)||q?.status!=='qualified'||q.installedIsolation!==true||q.privacy!==true||q.actualPTY!==true||!text(q.evidenceFilename,200)||!hash(q.evidenceSha256)||!text(q.testedPython,80)||!text(q.testedNode,80))fail();
 const python=runtime(runtimes?.python,'python',t),node=runtime(runtimes?.node,'node',t);
 const profile={version:1,preparationId:p.preparationId,artifactId:p.artifactId,grant:p.grant,origin:p.origin,expiresAt:p.expiresAt,name:p.name,caPem:p.caPem??'',artifact:{filename:a.filename,bytes:a.bytes,sha256:a.sha256,package:r.package,source:r.source,target:t,compatibility:r.compatibility,minimumGlibc:q.minimumGlibc},runtimes:{python,node}};
 const helper=readFileSync(new URL('./terminal-install-client.mjs',import.meta.url));
 const body=`#!/bin/bash
# Private Amplifier Unified terminal setup. Do not share this file.
set -euo pipefail
umask 077
unset NODE_OPTIONS NODE_PATH NODE_TLS_REJECT_UNAUTHORIZED
case "$(uname -s):$(uname -m)" in ${t.os==='macos'?'Darwin':'Linux'}:${t.arch==='arm64'?'arm64|'+(t.os==='macos'?'Darwin':'Linux')+':aarch64':'x86_64'}) ;; *) echo 'This setup file is for another platform.' >&2; exit 1;; esac
work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT
decode() { if [ "$(uname -s)" = Darwin ]; then /usr/bin/base64 -D; else base64 --decode; fi; }
checksum() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
decode > "$work/profile.json" <<'AMPLIFIER_PROFILE'
${encoded(JSON.stringify(profile))}
AMPLIFIER_PROFILE
decode > "$work/client.whl" <<'AMPLIFIER_WHEEL'
${encoded(artifact.wheelBytes)}
AMPLIFIER_WHEEL
decode > "$work/install.mjs" <<'AMPLIFIER_HELPER'
${encoded(helper)}
AMPLIFIER_HELPER
echo 'Preparing the verified terminal runtime…'
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 20 --max-time 600 --max-filesize ${node.bytes} ${quote(node.url)} -o "$work/node.tar.gz"
if [ "$(wc -c < "$work/node.tar.gz" | tr -d ' ')" != ${quote(String(node.bytes))} ] || [ "$(checksum "$work/node.tar.gz")" != ${quote(node.sha256)} ]; then echo 'Node runtime verification failed.' >&2; exit 1; fi
(ulimit -f 262144; tar -xzOf "$work/node.tar.gz" ${quote(node.archiveRoot+'/'+node.executable)} > "$work/node")
if [ "$(checksum "$work/node")" != ${quote(node.executableSha256)} ]; then echo 'Node executable verification failed.' >&2; exit 1; fi
chmod 700 "$work/node"
"$work/node" "$work/install.mjs" "$work/profile.json" "$work"
`;
 const bytes=Buffer.from(body);if(bytes.length>96*1024*1024)fail();return {filename:'amplifier-terminal-'+p.preparationId+'.sh',contentType:'text/x-shellscript; charset=utf-8',bytes,sha256:digest(bytes)};
}
