import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,realpath,readFile,writeFile,unlink} from 'node:fs/promises';
import {tmpdir,homedir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash,randomBytes} from 'node:crypto';
const execute=promisify(execFile),apiURL=process.env.DISTRIBUTION_OWNER_MODULE??new URL('../dist/index.js',import.meta.url).href;
const api=await import(apiURL);
const {createManualSystemdHandoffSource,createLinuxSystemdSourceObserver,launchExistingStateHandoff,
 ServiceLifecycleOwner,PosixOwnedProcessLifecycle,DistributionUpdateOwner,serveSupervisor,connectHostControlFile}=api;
const enabled=process.platform==='linux'&&process.env.DISTRIBUTION_SYSTEMD_TEST==='1'&&process.env.DISTRIBUTION_TEST_HOST_MODULE;
async function until(fn){for(let i=0;i<300;i++){if(await fn())return;await new Promise(r=>setTimeout(r,20));}throw Error('fixture_timeout');}
test('isolated systemd source retires with kernel exit then hands saved state to new owned real host',{skip:!enabled},async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'manual-sd-'))),unit='amplifier-handoff-test-'+root.split('/').at(-1)+'.service';
 const unitDir=join(homedir(),'.config/systemd/user');await mkdir(unitDir,{recursive:true});
 const unitFile=join(unitDir,unit),source=join(root,'s'),data=join(root,'data');await mkdir(data);await writeFile(join(data,'history'),'synthetic retained history');
 const bindings=[{id:'application',kind:'directory',path:data}],fixture=fileURLToPath(new URL('./manual-systemd-fixture.mjs',import.meta.url));
 const entryDigest=createHash('sha256').update(await readFile(fixture)).digest('hex');
 const target={identity:{id:'qualified-synthetic-fixture',version:'1.0.0',revision:'a'.repeat(40),digest:entryDigest},handle:'fixture'};
 const hostFile=join(root,'host.json'),hostTokenFile=join(root,'host-token'),hostToken=randomBytes(32).toString('hex'),supervisorFile=join(root,'supervisor.json');await writeFile(hostTokenFile,hostToken,{mode:0o600});
 const config={root,source,data,bindings,target,api:apiURL,real:true,unit,entryDigest,hostModule:process.env.DISTRIBUTION_TEST_HOST_MODULE,hostFile,hostTokenFile,hostToken,supervisorFile};
 const file=join(root,'source.json');await writeFile(file,JSON.stringify(config));
 // Explicit test-only user unit, disabled boot start, no restart, loopback port0.
 // Clean env has no provider credentials or user history. No existing unit is changed.
 await writeFile(unitFile,`[Unit]\nDescription=Isolated source handoff qualification\n[Service]\nType=simple\nExecStart=/usr/bin/env -i PATH=/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/${process.getuid()} ${process.execPath} ${fixture} ${file}\nRestart=no\nKillMode=control-group\nTimeoutStopSec=infinity\nSendSIGKILL=no\nUMask=0077\n[Install]\nWantedBy=default.target\n`,{flag:'wx',mode:0o600});
 const ctl=(...args)=>execute('systemctl',['--user',...args],{maxBuffer:65536});
 let lifecycle,service,owner,transport;
 t.after(async()=>{if(lifecycle?.ownedPid)await lifecycle.close().catch(()=>{});await service?.close();await transport?.close();await owner?.close();
  await ctl('stop',unit).catch(()=>{});await unlink(unitFile);await ctl('daemon-reload');
  // Retain only this test's synthetic state and receipts for independent review.
 });
 await ctl('daemon-reload');await ctl('start',unit);
 try{await until(async()=>{try{await readFile(join(root,'ready.json'));return true;}catch{return false;}});}catch(e){throw Error((await execute('journalctl',['--user-unit',unit,'-n','35','--no-pager'])).stdout);}
 const ready=JSON.parse(await readFile(join(root,'ready.json'),'utf8'));
 const observer=createLinuxSystemdSourceObserver({unit,python:'/usr/bin/python3'}),sourcePort=createManualSystemdHandoffSource({sourceDirectory:source,claimDirectory:join(root,'claim'),bindings,observer});
 const host=connectHostControlFile(hostFile,'fixture-scope'),destination=join(root,'destination.json');await writeFile(destination,JSON.stringify({...config,destination:true}));
 let spawned=0;
 lifecycle=new PosixOwnedProcessLifecycle({ownerId:'fixture-owner',stopMs:3000,readinessMs:5000,inspect:()=>host.inspect(),admitRestart:async()=>null,
  resolve:async()=>{spawned++;return {command:process.execPath,args:[fixture,destination]};}});
 const binding={installationId:'manual-fixture',ownerId:'fixture-owner',dataScope:'fixture-scope'};
 service=new ServiceLifecycleOwner({directory:join(root,'new-service'),...binding,lifecycle,host:host.service,releases:{verify:async()=>createHash('sha256').update(await readFile(fixture)).digest('hex')===entryDigest},currentRelease:()=>target});
 owner=new DistributionUpdateOwner({directory:join(root,'updates'),dataScope:'fixture-scope',initial:target,releases:{},lifecycle,preferences:{autoCheck:false,autoInstall:false,intervalMs:60000}});
 const superToken=randomBytes(32).toString('hex'),superTokenFile=join(root,'super-token');await writeFile(superTokenFile,superToken,{mode:0o600});
 transport=await serveSupervisor({owner,service,token:superToken});await writeFile(supervisorFile,JSON.stringify({schema:'distribution-supervisor-connection-v1',url:transport.url,tokenFile:superTokenFile}),{mode:0o600});
 await launchExistingStateHandoff({destination:service,source:sourcePort,bindings,command:{commandId:'handoff',stoppedCommandId:'source-stop',expected:ready.expected,target:target.identity,participantIds:['ingress']}});
 const receipt=await service.waitFor('handoff');
 await writeFile(join(root,'handoff-receipt.json'),JSON.stringify(receipt,null,2));
 assert.equal(receipt.status,'ready',JSON.stringify(receipt));assert.equal(receipt.admissionSettlement.state,'settled');assert.equal(spawned,1);
 assert.notEqual(receipt.observed.instanceId,ready.expected.instanceId);assert.equal(receipt.observed.releaseDigest,ready.expected.releaseDigest);
 assert.equal((await host.service.inspectServiceLifecycle()).intakeClosed,false);assert.equal((await readFile(join(data,'history'),'utf8')),'synthetic retained history');
 assert.equal(JSON.parse(await readFile(join(root,'destination-ready.json'),'utf8')).history,'synthetic retained history');
 await ctl('start',unit).catch(()=>{});await until(async()=>Number((await ctl('show','-p','MainPID','--value',unit)).stdout.trim())===0);
 assert.equal(spawned,1);assert.match((await ctl('show','-p','Result','--value',unit)).stdout,/exit-code/);
 service.stop({commandId:'stop-new',expected:receipt.observed});assert.equal((await service.waitFor('stop-new')).status,'stopped');
 const proof=await readFile(join(root,'claim/authority.json'),'utf8');assert.match(proof,/completed/);
 const evidence={schema:'manual-systemd-qualification-v1',node:process.version,systemd:(await execute('systemctl',['--version'])).stdout.split('\n')[0],
  sourceKernelExit:true,sourceDurableRetirement:true,sourceRelaunchRefused:true,realHostHeldOwners:['ingress'],destinationOwnedSpawnCount:spawned,sameRelease:true,sameRoots:true,historyPreserved:true,gateReconciled:true,
  limits:['Synthetic app and release fixture; actual product wrapper requires separately qualified release and full configured owner census.','No live application state, auth, unit, or ports were modified.'],root,unit};
 await writeFile(join(root,'acceptance.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));
});
