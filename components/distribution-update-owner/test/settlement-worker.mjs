import assert from 'node:assert/strict';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {createHost} from '@amplifier/unified-host';
import {DistributionUpdateOwner, SupervisorClient, HostControlClient, serveSupervisor,
  serveHostControl, createHostReleaseVerifier} from '@amplifier/unified-distribution-update-owner';

const root = process.argv[2], scope = 'settlement-fixture';
const key = randomBytes(32).toString('hex');
const identity = n => ({id:'release-'+n,version:n+'.0.0',revision:String(n).repeat(40),digest:String(n).repeat(64)});
const candidate = n => ({identity:identity(n),handle:'fixture-'+n});
const owners = ['portability','capability:attachments','workspaces','native-admin','application-updates',
  'capability:voice','capability:connectors','notifications','diagnostics','capability:observations',
  'capability:coordination','capability:worktrees','capability:publishing','capability:recall',
  'capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];
let host, control, transport, supervisor, running = {identity:identity(1),instanceId:'original',dataScope:scope,ready:true};
let restarts = 0, releases = [], pauseRelease = true, refuseRelease = false;
const hostClient = new HostControlClient({dataScope:scope,connect:()=>({url:control.url,token:key,dataScope:scope})});
const boot = async replacement => {
  const ids = replacement ? [...owners, 'native-message-metadata'] : owners;
  const participants = ids.map(id => ({id,
    acquire:async fence => ({ownerId:id,fenceId:fence.fenceId,release:async()=>{}}),
    reconcileRelease:async request => {
      assert.equal(request.proof.verified,true);
      assert.equal(request.proof.instanceId,running.instanceId);
      if (id === 'manual-preview-ingress' && pauseRelease) {
        pauseRelease = false;
        // Each participant stays below the host's 30 s limit, but the aggregate
        // release crosses the old 10 s transport deadline. Do not shorten this.
        await delay(11000);
      }
      releases.push(id);
    },
  }));
  host = await createHost({stateDirectory:join(root,'host'),allowedWorkspaceRoots:[root],
    engines:[{id:'unused',command:process.execPath,args:['-e','process.exit(2)']}],
    quiescence:{instanceId:running.instanceId,dataScope:scope,timeoutMs:30000,requiredOwners:ids,
      coverage:{},participants,verifyRelease:createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:()=>running})}});
  const port = {
    inspectQuiescence:()=>host.inspectQuiescence(),
    quiescenceReceipt:id=>host.quiescenceReceipt(id),
    admitQuiescence:request=>host.admitQuiescence(request),
    releaseQuiescence:request=>{
      if(refuseRelease) {refuseRelease=false;throw Error('fixture_lost_release');}
      return host.releaseQuiescence(request);
    },
  };
  control = await serveHostControl({host:port,inspectRunning:()=>running,token:key});
};
const owner = new DistributionUpdateOwner({directory:join(root,'supervisor'),dataScope:scope,
  initial:candidate(1),preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},
  releases:{check:async()=>({releases:[identity(1),identity(2),identity(3)],recommendedId:'release-2'}),
    prepare:async id=>({identity:id,handle:id.id}),verify:async()=>true},
  lifecycle:{inspect:()=>hostClient.inspect(),admitRestart:hostClient.admitRestart,reconcileAdmission:hostClient.reconcileAdmission,
    restart:async request=>{
      restarts++;
      await control.close();await host.close();
      running={identity:request.target.identity,instanceId:request.instanceId,dataScope:scope,ready:true};
      await boot(true);
    }},
  onChange:receipt=>transport?.publish(receipt),
});
try {
  transport=await serveSupervisor({owner,token:key});
  supervisor=new SupervisorClient({url:transport.url,token:key});
  await boot(false);
  await supervisor.owner.check('check');await owner.waitFor('check');
  const automaticStarted=performance.now();
  await supervisor.owner.install('automatic-install','release-2');
  const automatic = await owner.waitFor('automatic-install');
  const automaticMs=Math.round(performance.now()-automaticStarted);
  assert.ok(automaticMs>10000);
  assert.equal(automatic.admissionSettlement?.state,'settled',JSON.stringify(automatic));
  assert.equal((await hostClient.inspectQuiescence()).intakeClosed,false);
  assert.equal(releases.length,20);
  assert.equal(releases.includes('native-message-metadata'),false);
  assert.equal(restarts,1);
  // A retained unknown requires exact explicit reconciliation, never a second
  // restart. The same long release must also work through the outer RPC client.
  refuseRelease=true;pauseRelease=true;releases=[];
  await supervisor.owner.install('recovery-install','release-3');
  const uncertain=await owner.waitFor('recovery-install');
  assert.equal(uncertain.admissionSettlement.state,'unknown');
  assert.equal((await hostClient.inspectQuiescence()).intakeClosed,true);
  const reconcileStarted=performance.now();
  const reconciled=await supervisor.owner.reconcile('recovery-install');
  const reconciliationMs=Math.round(performance.now()-reconcileStarted);
  assert.ok(reconciliationMs>10000);
  assert.equal(reconciled.admissionSettlement.state,'settled');
  assert.equal(restarts,2);
  assert.equal(releases.length,21);
  assert.equal((await hostClient.inspectQuiescence()).intakeClosed,false);
  await supervisor.owner.reconcile('recovery-install');
  assert.equal(restarts,2);assert.equal(releases.length,21);
  console.log(JSON.stringify({schema:'distribution-settlement-installed-v1',automaticSettlement:automatic.admissionSettlement.state,
    reconciliationSettlement:reconciled.admissionSettlement.state,originalOwners:20,replacementOwners:21,
    automaticMs,reconciliationMs,releaseLongerThanTenSeconds:true,exactProof:true,actualHostLedgerReopened:true,noReplay:true,
    independentOwnerAndHostPackages:true,syntheticParticipants:true,signedLauncher:false,actualProcessReplacement:false,liveDeployment:false}));
} finally {
  hostClient.close();supervisor?.close();
  await control?.close();await host?.close();await transport?.close();await owner.close();
}
