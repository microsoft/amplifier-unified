import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,copyFile,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.AMPLIFIER_ACP_PYTHON,ownerPython=process.env.UNIFIED_OWNERS_PYTHON;
const setup=String.raw`
import importlib.util,json,sys,yaml
from pathlib import Path
p=Path(sys.argv[1]);home=p/'home';home.mkdir();provider=p/'provider';provider.mkdir();module=provider/'amplifier_module_provider_transfer_fixture';module.mkdir()
(module/'__init__.py').write_bytes(Path(sys.argv[2]).read_bytes());(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-transfer-fixture"\nversion="0.1.0"\n')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
config={'reasoning_effort':'low','readinessAudit':str(p/'probe.txt'),'normalAudit':str(p/'normal.txt')}
bundle=p/'bundle.yaml';bundle.write_text(yaml.safe_dump({'bundle':{'name':'transfer-fixture','version':'1.0.0'},'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple','source':str(context)}},'providers':[{'module':'provider-transfer-fixture','source':str(provider),'config':config}]}))
settings={'bundle':{'active':'transfer-fixture','app':[]},'config':{'providers':[{'id':'fixture','module':'provider-transfer-fixture','source':str(provider),'config':config}]}}
(home/'settings.yaml').write_text(yaml.safe_dump(settings))
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':90,'transferAuthorityDirectory':str(p/'distribution/capabilities/portability'),'transferWorkspaceRoots':[str(p)],'transferProbeCommand':[sys.executable,'-I','-m','amplifier_acp.native.portability_probe']}))
`;
class Peer{
 constructor(socket){this.socket=socket;this.seq=0;this.pending=new Map();socket.on('message',raw=>{const msg=JSON.parse(raw),p=this.pending.get(msg.id);if(!p)return;this.pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(Error(msg.error.message)):p.resolve(msg.result);});}
 async request(method,params){const id=++this.seq;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Paired graph timeout: '+method)),90000);this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});}
 static async open(url){const socket=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin:url});await once(socket,'open');const peer=new Peer(socket);await peer.request('initialize',{channel:'ahp-root://',clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return peer;}
 action(scope,operation,args,commandId=randomUUID()){return this.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'portability',operation:'portability.'+operation,version:1,args:{...(scope!=='ahp-root://'?{sessionId:scope}:{}),...args},commandId}).then(result=>result.result).catch(error=>{throw Error(operation+': '+error.message);});}
 close(){this.socket.terminate();for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('Closed'));}this.pending.clear();}
}
const git=(path,...args)=>execFileSync('git',['-c','core.hooksPath=/dev/null','-C',path,...args],{encoding:'utf8'}).trim();
async function side(root){await mkdir(root);await mkdir(join(root,'workspace'));await mkdir(join(root,'web'));await writeFile(join(root,'web/index.html'),'<html><head></head><body>Owned transfer</body></html>');const setupResult=spawnSync(python,['-I','-c',setup,root,fileURLToPath(new URL('./fixtures/transfer_provider.py',import.meta.url))],{encoding:'utf8'});assert.equal(setupResult.status,0,setupResult.stderr);return {root,workspace:join(root,'workspace'),config:{account:'paired-fixture',stateDirectory:join(root,'distribution'),webDirectory:join(root,'web'),defaultWorkspace:join(root,'workspace'),allowedWorkspaceRoots:[root],engines:[{id:'amplifier',command:python,args:['-I','-m','amplifier_acp','--config',join(root,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(root,'writers')}}],operations:{python:ownerPython},portability:{python:ownerPython,engines:['amplifier'],stageDir:join(root,'stages'),exchangeDir:join(root,'exchange')}}};}
test('two installed distributions preserve canonical history and inactive owner evidence through signed transfer',{skip:!python||!ownerPython,timeout:240000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'unified-paired-')));let source,target,sp,tp;
 try{
  const a=await side(join(directory,'source')),b=await side(join(directory,'target'));git(a.workspace,'init','-q');git(a.workspace,'config','user.email','fixture@example.invalid');git(a.workspace,'config','user.name','Owned transfer fixture');await writeFile(join(a.workspace,'retained.txt'),'Unchanged source\n');git(a.workspace,'add','.');git(a.workspace,'commit','-qm','Owned source');git(b.workspace,'clone',a.workspace,'.');
  source=await createDistribution(a.config);target=await createDistribution(b.config);sp=await Peer.open(source.url);tp=await Peer.open(target.url);
  const pa=await sp.action('ahp-root://','inspect',{}),pb=await tp.action('ahp-root://','inspect',{});
  const pair=spawnSync(ownerPython,['-I','-c',"import json,sys;from pathlib import Path;from amplifier_worktrees.git import atomic;a,b=map(Path,sys.argv[1:3]);x,y=map(json.loads,sys.argv[3:]);atomic(a/'peers.json',{y['id']:y});atomic(b/'peers.json',{x['id']:x})",join(a.root,'distribution/capabilities/portability'),join(b.root,'distribution/capabilities/portability'),JSON.stringify(pa.host),JSON.stringify(pb.host)],{encoding:'utf8'});assert.equal(pair.status,0,pair.stderr);
  const session='ahp-session:/'+randomUUID();await sp.request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(a.workspace).href]});await source.host.nativeControl(session,'provider.select',{provider:'fixture',model:'fixture-model',effort:'low'});
  await source.host.submitTurn(session,{commandId:'original-input',clientId:'fixture',text:'Retain the conversation and never replay this input.'});assert.equal((await source.host.waitForTurn(session,'original-input',30000)).status,'completed');
  await sp.request('x-amplifier/capabilityAction',{channel:session,topic:'questions',operation:'question.create',version:1,commandId:'retained-question',args:{prompt:'Retain this unanswered decision.',required:true,dependency:'Preserved transfer evidence'}});
  await sp.request('x-amplifier/capabilityAction',{channel:session,topic:'canvas',operation:'canvas.show',version:1,commandId:'retained-artifact',args:{kind:'text',content:'Historical artifact bytes'}});
  const before=await source.host.inspectSession(session);const metadata=spawnSync(python,['-I','-c',"import json,sys;from pathlib import Path;import yaml;p=Path(sys.argv[1]);sid=sys.argv[2];native=next((p/'home/projects').glob('*/sessions/'+sid));m=json.loads((native/'metadata.json').read_text());print(json.dumps({'bundle':m.get('bundle') or m.get('bundle_name'),'native':str(native)}))",a.root,before.nativeSessionId],{encoding:'utf8'});assert.equal(metadata.status,0,metadata.stderr);const canonical=JSON.parse(metadata.stdout),original=await readFile(join(canonical.native,'transcript.jsonl'));
  // Destination fixture is provisioned independently, with the same declared bundle identity.
  const configured=spawnSync(python,['-I','-c',"import sys,yaml;from pathlib import Path;p=Path(sys.argv[1]);d=yaml.safe_load(p.read_text());d['bundle']['active']=sys.argv[2];p.write_text(yaml.safe_dump(d))",join(b.root,'home/settings.yaml'),canonical.bundle],{encoding:'utf8'});assert.equal(configured.status,0,configured.stderr);
  const sourceProviderAudit=await readFile(join(a.root,'normal.txt'));
  const reviewed=await sp.action(session,'inspect',{sessionId:session});
  const outgoing=await sp.action(session,'export',{sessionId:session,destination:pb.host.id,sourceRevision:reviewed.source.sourceRevision,expectedExecutionRevision:before.executionRevision,mode:'clean',reviewedContent:true},'owned-export');assert.equal(outgoing.phase,'prepared');
  const incomingFile=join(b.root,'exchange/incoming.json');await copyFile(outgoing.package,incomingFile);const incoming=await tp.action('ahp-root://','stage',{path:incomingFile,repository:b.workspace,reviewedCapsuleHash:outgoing.review.capsuleHash},'owned-stage');assert.equal(incoming.phase,'ready');assert.equal(target.host.diagnostics().activeAgents,0);
  const ready=join(a.root,'exchange/ready.json');await copyFile(incoming.receiptPath,ready);const released=await sp.action(session,'release',{sessionId:session,id:outgoing.id,expectedRevision:outgoing.revision,path:ready,reviewedCapsuleHash:outgoing.review.capsuleHash},'owned-release');assert.equal(released.phase,'released');
  const certificate=join(b.root,'exchange/release.json');await copyFile(released.receiptPath,certificate);const activated=await tp.action('ahp-root://','activate',{id:incoming.id,expectedRevision:incoming.revision,path:certificate},'owned-activate');assert.equal(activated.phase,'active');assert.equal(activated.adoption.uri,session);assert.equal(target.host.diagnostics().activeAgents,0);assert.ok(activated.evidenceImport.receipts.every(row=>row.executionAuthority===false&&row.replayed===false));
  const evidencePage=await tp.action(session,'evidence',{id:incoming.id,section:'unified.operations',limit:4000});assert.equal(JSON.parse(evidencePage.text).records.find(row=>row.kind==='question').value.status,'pending');
  const questions=await tp.request('x-amplifier/capabilityAction',{channel:session,topic:'questions',operation:'question.list',version:1,commandId:'read-destination-questions',args:{limit:5}});assert.deepEqual(questions.result.items,[]);
  const artifacts=await target.resources.readTransferEvidence({session,transferId:incoming.id});assert.equal(artifacts.items[0].historicalOnly,true);assert.equal(target.resources.store.list(session).items.length,0);
  const adopted=await target.host.inspectSession(session);assert.equal(adopted.nativeSessionId,before.nativeSessionId);assert.equal(await readFile(join(adopted.executionDirectory,'retained.txt'),'utf8'),'Unchanged source\n');assert.deepEqual(await readFile(join(canonical.native,'transcript.jsonl')),original);
  const check=spawnSync(python,['-I','-c',"import sys;from pathlib import Path;p=Path(sys.argv[1]);sid=sys.argv[2];native=next((p/'home/projects').glob('*/sessions/'+sid));sys.stdout.buffer.write((native/'transcript.jsonl').read_bytes())",b.root,before.nativeSessionId]);assert.equal(check.status,0,check.stderr.toString());assert.deepEqual(check.stdout,original);
  await assert.rejects(source.host.submitTurn(session,{commandId:'forbidden-source',clientId:'fixture',text:'Must remain fenced'}),/transfer|fenced|released/i);assert.deepEqual(await readFile(join(a.root,'normal.txt')),sourceProviderAudit);await assert.rejects(readFile(join(b.root,'normal.txt')),/ENOENT/);assert.equal((await readFile(join(b.root,'probe.txt'),'utf8')).trim().split('\n').length,2);
 }finally{sp?.close();tp?.close();await source?.close();await target?.close();await rm(directory,{recursive:true,force:true});}
});
