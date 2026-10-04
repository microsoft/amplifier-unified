import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,realpath,writeFile,readFile,mkdir,lstat,symlink,unlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createLinuxLegacyMaintenanceCustody} from '../dist/legacy-systemd-custody.js';
const execute=promisify(execFile),enabled=process.platform==='linux'&&process.env.DISTRIBUTION_LEGACY_SYSTEMD_TEST==='1';
async function stamp(pid){const raw=await readFile(`/proc/${pid}/stat`,'utf8');return {pid,startTicks:raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/)[19]};}
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await delay(25);}throw Error('fixture_timeout');}
for(const inhibited of [false,true])test(`actual transient KillMode=process custody with ${inhibited?'bound inhibition profile':'strict original profile'}`,{skip:!enabled,timeout:15000},async t=>{
  const root=await realpath(await mkdtemp(join(tmpdir(),'legacy-custody-'))),unit=`amplifier-maintenance-test-${root.split('/').at(-1)}.service`;
  const file=join(root,'fixture.mjs'),ready=join(root,'ready.json');
  await writeFile(file,`import {spawn} from 'node:child_process';import {writeFile} from 'node:fs/promises';
const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
await writeFile(process.argv[2],JSON.stringify({pid:process.pid,child:child.pid}));setInterval(()=>{},1000);\n`);
  const ctl=(...args)=>execute('systemctl',['--user',...args],{maxBuffer:65536});
  let bound,exclude=true,maskPath;const assertExclusion=async()=>{if(!exclude)throw Error('fixture_exclusion_lost');};
  t.after(async()=>{bound?.close();await ctl('kill','--kill-whom=all','--signal=KILL',unit).catch(()=>{});
    await ctl('thaw',unit).catch(()=>{});await ctl('stop',unit).catch(()=>{});await ctl('reset-failed',unit).catch(()=>{});
    if(maskPath){await unlink(maskPath).catch(e=>{if(e.code!=='ENOENT')throw e;});await ctl('daemon-reload');}});
  await execute('systemd-run',['--user',`--unit=${unit}`,'--property=Type=simple','--property=KillMode=process',
    '--property=Restart=no','--property=Delegate=no','/usr/bin/env','-i','PATH=/usr/bin:/bin',process.execPath,file,ready],{maxBuffer:65536});
  await until(async()=>{try{return Boolean(JSON.parse(await readFile(ready,'utf8')).child);}catch{return false;}});
  const p=JSON.parse(await readFile(ready,'utf8')),required=await Promise.all([p.pid,p.child].map(stamp));
  const options={unit,python:'/usr/bin/python3',expectedMainPid:p.pid,requiredProcesses:required,assertExclusion};
  const observer=createLinuxLegacyMaintenanceCustody(options);
  await assert.rejects(observer.bind(),/not_frozen/);
  await ctl('freeze',unit);
  await assert.rejects(createLinuxLegacyMaintenanceCustody({...options,requiredProcesses:[...required,await stamp(process.pid)]}).bind(),/outside_custody/);
  await assert.rejects(createLinuxLegacyMaintenanceCustody({...options,requiredProcesses:required.map(r=>({...r,startTicks:'1'}))}).bind(),/outside_custody/);
  let selected=observer;
  if(inhibited){
    const original=await observer.capture();
    const folder=`/run/user/${process.getuid()}/systemd/user.control`;await mkdir(folder,{recursive:true});
    maskPath=join(folder,unit);await symlink('/dev/null',maskPath);await ctl('daemon-reload');
    const s=await lstat(maskPath,{bigint:true}),mask={path:maskPath,dev:s.dev.toString(),ino:s.ino.toString(),uid:process.getuid(),target:'/dev/null'};
    const inhibition={original,mask,assertHeld:assertExclusion};
    await assert.rejects(observer.bind(),/policy_unqualified/);
    await assert.rejects(createLinuxLegacyMaintenanceCustody({...options,inhibition:{...inhibition,mask:{...mask,ino:'1'}}}).bind(),/inhibition_changed/);
    await assert.rejects(async()=>createLinuxLegacyMaintenanceCustody({...options,inhibition:{...inhibition,original:{...original,mainPid:1}}}).bind(),/profile_invalid/);
    selected=createLinuxLegacyMaintenanceCustody({...options,inhibition});
  }
  bound=await selected.bind();assert.equal(bound.witness.killMode,'process');assert.equal(bound.witness.processes.length,2);
  assert.deepEqual(bound.witness.processes.map(x=>x.pid).sort((a,b)=>a-b),[p.pid,p.child].sort((a,b)=>a-b));
  let exited=false;bound.exited.then(()=>exited=true);
  // KillMode=process is deliberately reproduced. Main exit must not authorize
  // release while its original child remains frozen and alive in the cgroup.
  await ctl('kill','--kill-whom=main','--signal=KILL',unit);
  if(!inhibited){
    await delay(80);assert.equal(exited,false);await assert.rejects(bound.confirmExited(),/pidfd_exit_unconfirmed/);
  }
  // A mask reload may change systemd's effective KillMode to control-group;
  // frozen descendants still cannot handle SIGTERM. The explicit interruption
  // kills the entire owned group; confirm only after every retained pidfd exits.
  await ctl('kill','--kill-whom=all','--signal=KILL',unit);
  await bound.exited;
  // systemd can still remove the now-empty ControlGroup property between the
  // two read-only samples. Wait for a stable observation; never relax proof.
  await until(async()=>{try{await bound.confirmExited();return true;}catch(e){if(!/maintenance_unit_exit_changed/.test(e.message))throw e;return false;}});
  exclude=false;await assert.rejects(bound.confirmExited(),/exclusion_lost/);exclude=true;
  // A later invocation of the same service name cannot inherit old exit proof.
  if(inhibited){
    await assert.rejects(ctl('start',unit));await assert.rejects(ctl('restart',unit));
    await bound.confirmExited();
  }else{
    await ctl('start',unit);await until(async()=>Number((await ctl('show','--property=MainPID','--value',unit)).stdout)>0);
    await assert.rejects(bound.confirmExited(),/unit_exit_unconfirmed/);
  }
  const receipt={schema:'legacy-systemd-custody-fixture-v1',root,unit,node:process.version,
    actualKernelPidfds:2,transientUnit:true,killMode:'process',mainExitInsufficient:!inhibited,
    wholeGroupExitConfirmed:true,replacementInvocationRejected:!inhibited,requiredOutsideWriterRejected:true,
    boundInhibitionProfile:inhibited,maskedStartRestartDenied:inhibited,
    limits:['Owned synthetic processes only; no installed service or business histories touched.',
      'External exclusion is test-owned; production writer exclusion and retained launcher remain separately reviewed adapters.']};
  await writeFile(join(root,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
});
