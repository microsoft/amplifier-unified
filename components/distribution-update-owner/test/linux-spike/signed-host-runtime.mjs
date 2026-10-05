// Test-signed real Host entrypoint. Uses no user history, provider, or account.
import {createServer} from 'node:net';
import {readFile,writeFile,unlink,appendFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHost} from '@amplifier/unified-host';
import {createRuntimeIdentity,connectSupervisorFileLazy,serveHostControl,
  createHostServiceReleaseVerifier,createHostServiceInitialStartVerifier,inspectPristineInstallation} from '@amplifier/unified-distribution-update-owner';
const root=process.env.FIXTURE_ROOT;
let initialized=false;
const runtime=await createRuntimeIdentity({entrypointUrl:import.meta.url,
  trustedKeys:JSON.parse(process.env.FIXTURE_TRUSTED_KEYS),isReady:()=>initialized});
const authorityFile=process.env.AMPLIFIER_DISTRIBUTION_PROVISIONING_AUTHORITY ?? process.env.FIXTURE_PROVISIONING_AUTHORITY;
const origin=authorityFile ? await inspectPristineInstallation(authorityFile) : undefined;
const supervisor=connectSupervisorFileLazy(origin?.supervisorDiscoveryFile??join(root,'supervisor.json'));
const serviceIdentity={installationId:process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID,
  ownerId:process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID,dataScope:runtime.dataScope,
  instanceId:runtime.instanceId,releaseDigest:runtime.identity.digest};
const peer=fileURLToPath(import.meta.resolve('@amplifier/unified-host')).replace(/dist\/index.js$/,'fixtures/acp-peer.mjs');
const host=await createHost({stateDirectory:origin?.applicationStateDirectory??join(root,'host-state'),allowedWorkspaceRoots:[root],
  engines:[{id:'fixture',command:process.execPath,args:[peer],env:{FIXTURE_LOG:join(root,'dispatches.jsonl')}}],
  nativeHostRequest:async context=>host.invokeClientTool(context.session,'fixture-client','inspect-ui',{}),
  quiescence:{instanceId:runtime.instanceId,dataScope:runtime.dataScope,requiredOwners:[],participants:[],coverage:{},
    verifyRelease:async()=>{throw Error('not-a-distribution-update-fixture');},
    serviceLifecycle:{identity:serviceIdentity,
      ...(origin?.initialInstanceId===runtime.instanceId?{
        initialStart:{commandId:origin.initialCommandId,identity:serviceIdentity},
        verifyInitialStart:createHostServiceInitialStartVerifier({service:supervisor.service,
          inspectRunningService:async()=>{await runtime.inspectRunning();return serviceIdentity;}}),
      }:{}),verifyRelease:createHostServiceReleaseVerifier({service:supervisor.service,
      inspectRunningService:async()=>{await runtime.inspectRunning();return serviceIdentity;}})}}});
initialized=true;
const control=await serveHostControl({host,inspectRunning:async()=>({...await runtime.inspectRunning(),
  invocationId:process.env.INVOCATION_ID,intakeClosed:host.inspectServiceLifecycle().intakeClosed}),
  token:await readFile(origin?.hostTokenFile??join(root,'host-token'),'utf8'),discovery:{file:origin?.hostDiscoveryFile??join(root,'host.json'),
    tokenFile:origin?.hostTokenFile??join(root,'host-token'),dataScope:runtime.dataScope}});
const socketPath=join(root,'fixture.sock');
await unlink(socketPath).catch(e=>{if(e.code!=='ENOENT')throw e;});
const socket=createServer(s=>{let data='';s.on('data',chunk=>{data+=chunk;if(!data.includes('\n'))return;
  s.pause();void(async()=>{
    const request=JSON.parse(data);
    if(request.op==='inspect')return {...await runtime.inspectRunning(),hostUrl:host.url,
      invocationId:process.env.INVOCATION_ID,intakeClosed:host.inspectServiceLifecycle().intakeClosed};
    if(request.op==='receipt')return host.store.receipt(request.commandId)??null;
    if(request.op==='seed-unknown'){
      host.store.admission('ahp-chat:/saved-unknown',{text:'Uncertain external effect'},'saved-unknown','fixture',-1);
      host.store.finish('saved-unknown','unknown','Retained external uncertainty');
      return host.store.receipt('saved-unknown');
    }
    if(request.op==='clients')return host.store.get(request.session)?.state.activeClients??[];
    throw Error('unknown-fixture-request');
  })().then(result=>s.end(JSON.stringify({result})+'\n'),e=>s.end(JSON.stringify({error:String(e)})+'\n'));
});});
await new Promise(resolve=>socket.listen(socketPath,resolve));
await appendFile(join(root,'launches.jsonl'),JSON.stringify({identity:runtime.identity,instanceId:runtime.instanceId,
  invocationId:process.env.INVOCATION_ID,intakeClosed:host.inspectServiceLifecycle().intakeClosed})+'\n');
let closing=false;
process.on('SIGTERM',()=>{if(closing)return;closing=true;void(async()=>{
  await control.close();await new Promise(resolve=>socket.close(resolve));await host.close();supervisor.close();process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});});
