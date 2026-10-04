import {writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
export const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
// Explicit inert bytes, not a releasable wheel or runtime. These fixtures verify
// enrollment/hash contracts only; actual client qualification belongs to TUI.
export async function artifactFixture(directory){
 await mkdir(directory,{recursive:true});const filename='amplifier_unified_client_tui-0.5.0rc2-py3-none-linux_aarch64.whl',wheelBytes=Buffer.from('INERT Terminal fixture wheel; no executable code');
 const compatibility={ahpVersions:['0.9.0'],sdkVersion:'0.9.0',pythonRequires:'>=3.11',nodeRequires:'>=22'},source={repository:'https://github.com/microsoft/amplifier-unified-client-tui',commit:'a'.repeat(40)},pkg={name:'amplifier-unified-client-tui',version:'0.5.0rc2'};
 const wheel={filename,bytes:wheelBytes.length,sha256:hash(wheelBytes),target:{os:'linux',arch:'arm64',wheelTag:'py3-none-linux_aarch64',osFloor:null}};
 const qualification={status:'qualified',installedIsolation:true,privacy:true,actualPTY:true,testedPython:'3.13.0',testedNode:'22.22.0',testedOS:'INERT FIXTURE',minimumGlibc:'2.39'};
 const names=['protocol-install','protocol-build','native-build','native-tests','ruff','direction','current-tests','wheel-build','install-env','install-wheel','installed-isolation','installed-terminal'];
 const evidence={format:'amplifier-unified-client-tui.release.qualification',schemaVersion:1,package:pkg,source,compatibility,wheel,qualification,checks:Object.fromEntries(names.map(n=>[n,'passed'])),checkLogHashes:Object.fromEntries(names.map(n=>[n,'b'.repeat(64)])),limits:['Inert synthetic contract fixture; not a qualified release']};
 const evidenceBytes=Buffer.from(JSON.stringify(evidence)),evidencePath=join(directory,'evidence.json'),manifestPath=join(directory,'manifest.json'),wheelPath=join(directory,filename);
 const release={format:'amplifier-unified-client-tui.release',schemaVersion:1,package:pkg,source,compatibility,artifacts:[{...wheel,qualification:{...qualification,evidenceFilename:filename.slice(0,-4)+'.qualification.json',evidenceSha256:hash(evidenceBytes)}}]};
 const manifestBytes=Buffer.from(JSON.stringify(release));await writeFile(manifestPath,manifestBytes);await writeFile(evidencePath,evidenceBytes);await writeFile(wheelPath,wheelBytes);
 const runtime=(kind,version)=>({version,url:'https://artifacts.invalid/'+kind+'.tar.gz',bytes:100,sha256:'c'.repeat(64),archive:'tar.gz',archiveRoot:kind,executable:'bin/'+kind,target:{os:'linux',arch:'arm64'},...(kind==='node'?{executableSha256:'d'.repeat(64)}:{})});
 return {entry:{id:'fixture-linux-arm64',platform:'linux-arm64',artifact:{manifestPath,manifestSha256:hash(manifestBytes),filename,wheelPath,evidencePath},runtimes:{python:runtime('python','3.13.0'),node:runtime('node','22.22.0')}},release,evidence,wheelBytes};
}
export function inertRenderer({preparation}){const bytes=Buffer.from('# INERT PRIVATE FIXTURE\n'+JSON.stringify(preparation));return {bytes,sha256:hash(bytes),filename:'amplifier-terminal-fixture.sh',contentType:'text/x-shellscript; charset=utf-8'};}
