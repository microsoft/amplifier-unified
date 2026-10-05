import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {SessionStatus} from '@microsoft/agent-host-protocol';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
const {AdminConnection}=await import(process.env.AMPLIFIER_NATIVE_CAPABILITY_MODULE??'@amplifier/unified-native-capabilities');
const {composeNaming}=await import(process.env.AMPLIFIER_NAMING_COMPOSITOR??'../src/naming.js');
const python=process.env.INITIAL_SELECTION_PYTHON,source=process.env.NATIVE_ADMISSION_SOURCE,tests=process.env.NATIVE_ADMISSION_TESTS,hostModule=process.env.AMPLIFIER_NAMING_HOST;
async function until(check){const end=Date.now()+10000;while(Date.now()<end){if(await check())return;await new Promise(r=>setTimeout(r,10));}throw Error('Late title fixture deadline');}
const exists=async path=>{try{await readFile(path);return true;}catch{return false;}};
for(const manual of [false,true])test(`actual Native late automatic title reaches Host and AHP (${manual?'newer manual wins':'generated title'})`,{skip:!python||!source||!tests||!hostModule,timeout:60000},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'actual-late-title-'))),env={NATIVE_ADMISSION_SOURCE:source,NATIVE_ADMISSION_TESTS:tests};
 const setup=JSON.parse(execFileSync(python,['-c',`import json,sys,shutil
from pathlib import Path
from native_setup import configuration
root=Path(sys.argv[1]);config,cwd=configuration(root,idleSeconds=60)
source=Path(__import__('native_setup').__file__).parent/'fixtures/provider';provider=root/'provider';shutil.copytree(source,provider)
p=provider/'amplifier_module_provider_fixture/__init__.py';s=p.read_text();needle='    async def complete(self,request,**kwargs):\\n'
s=s.replace(needle,needle+'''        import json
        audit=Path(self.config['lateTitleAudit'])
        count=len(audit.read_text().splitlines()) if audit.exists() else 0
        with audit.open('a') as output:output.write('inert provider completion\\\\n')
        if count:
            Path(self.config['lateTitleEntered']).touch()
            async with asyncio.timeout(15):
                while not Path(self.config['lateTitleRelease']).exists():await asyncio.sleep(.01)
            return ChatResponse(content=[TextBlock(text=json.dumps({'action':'set','name':'Owned late automatic title','description':'Inert local provider'}))],finish_reason='stop',usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
''',1);p.write_text(s)
settings=json.loads(config.read_text());bundle=Path(settings['bundle']);s=bundle.read_text().replace(str(source.resolve()),str(provider.resolve()))
values={'lateTitleAudit':str(root/'audit'),'lateTitleEntered':str(root/'entered'),'lateTitleRelease':str(root/'release')};s=s.replace('    config: {}','    config: '+json.dumps(values));bundle.write_text(s)
from amplifier_acp.native.session_files import project_slug
settings['adminWorkspaceRoots']=[str(cwd)];config.write_text(json.dumps(settings));print(json.dumps({'config':str(config),'cwd':str(cwd),'history':str(Path(settings['home'])/'projects'/project_slug(cwd)/'sessions'),**values}))`,root],{env:{...process.env,...env},encoding:'utf8'}));
 const admin=new AdminConnection({command:python,args:['-I','-B','-m','amplifier_acp','--config',setup.config],env,cwd:setup.cwd,resolveWorkspace:()=>setup.cwd});let host,client,naming;const extensions=[];
 try{
  naming=await composeNaming({admin,engineId:'native',host:()=>host,inspectSession:s=>host.inspectSession(s),hostPortsSupported:true});assert.equal(naming.available,true);
  const {createHost}=await import(pathToFileURL(hostModule).href);
  host=await createHost({stateDirectory:join(root,'host'),allowedWorkspaceRoots:[setup.cwd],engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',setup.config],env,sessionMetadata:naming.sessionMetadata}],nativeEvent:async(context,params)=>{extensions.push(params.event);}});
  client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'late-title',protocolVersions:['0.9.0']});const session='ahp-session:/'+randomUUID();
  const rootSub=await client.subscribe('ahp-root://'),rootEvents=[];void(async()=>{for await(const e of rootSub.subscription)rootEvents.push(e);})();
  await client.request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(setup.cwd).href]});const sub=await client.subscribe(session),actions=[];void(async()=>{for await(const e of sub.subscription)if(e.type==='action')actions.push(e.params);})();
  await host.submitTurn(session,{commandId:'one-owned-turn',text:'One explicit inert local turn',clientId:'late-title'});assert.equal((await host.waitForTurn(session,'one-owned-turn')).completed,true);await until(()=>exists(setup.lateTitleEntered));
  assert.equal(host.store.get(session).summary.status,SessionStatus.Idle);const titleBefore=host.store.get(session).state.title;
  const nativeId=host.store.get(session).nativeSessionId,metadata=join(setup.history,nativeId,'metadata.json'),transcript=join(setup.history,nativeId,'transcript.jsonl');const before=await readFile(transcript);
  if(manual){client.dispatch(session,{type:'session/titleChanged',title:'Keep newer manual title'});await until(()=>host.store.get(session).state.title==='Keep newer manual title');}
  await writeFile(setup.lateTitleRelease,'release');
  if(manual){await until(()=>extensions.some(e=>e?.type==='session.naming'&&e.name==='Keep newer manual title'));assert.equal(host.store.get(session).state.title,'Keep newer manual title');const saved=JSON.parse(await readFile(metadata,'utf8'));assert.equal(saved.name,'Keep newer manual title');assert.equal(saved.name_source,'manual');assert.equal(actions.some(e=>e.action.type==='session/titleChanged'&&e.action.title==='Owned late automatic title'),false);}
  else{try{await until(()=>actions.some(e=>e.action.type==='session/titleChanged'&&e.action.title==='Owned late automatic title'));}catch(error){console.log(JSON.stringify({causalFailure:true,canonicalTitle:JSON.parse(await readFile(metadata,'utf8')).name,hostTitle:host.store.get(session).state.title,customNamingExtension:extensions.some(e=>e?.type==='session.naming'),inertProviderRequests:(await readFile(setup.lateTitleAudit,'utf8')).trim().split('\n').length}));throw error;}await until(()=>rootEvents.some(e=>e.type==='sessionSummaryChanged'&&e.params.session===session&&e.params.changes.title==='Owned late automatic title'));assert.equal(host.store.get(session).summary.title,'Owned late automatic title');assert.equal(JSON.parse(await readFile(metadata,'utf8')).name,'Owned late automatic title');await until(()=>extensions.some(e=>e?.type==='session.naming'));}
  assert.deepEqual(await readFile(transcript),before);assert.equal((await readFile(setup.lateTitleAudit,'utf8')).trim().split('\n').length,2);assert.deepEqual(naming.diagnostics(),{pending:0,failures:0,dropped:0});
  const reload=await client.subscribe(session);assert.equal(reload.result.snapshot.state.title,manual?'Keep newer manual title':'Owned late automatic title');
  console.log(JSON.stringify({manual,session,nativeId,titleBefore,finalTitle:host.store.get(session).state.title,turnCompletedBeforeNamingRelease:true,inertProviderRequests:2,canonicalTranscriptUnchanged:true,rootSummaryNotification:!manual,customExtensionPreserved:extensions.some(e=>e?.type==='session.naming')}));
 }finally{await writeFile(setup.lateTitleRelease,'release').catch(()=>{});await client?.shutdown();await naming?.close();await host?.close();await admin.close();await rm(root,{recursive:true,force:true});}
});
