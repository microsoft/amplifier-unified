import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile, readdir, open, lstat, readlink, realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {join, normalize, dirname} from 'node:path';
import {createHash} from 'node:crypto';
import {legacyRecoveryDigest, type LegacyMaintenanceCustody} from './legacy-process-recovery.js';

const execute = promisify(execFile);
const properties = ['Id','LoadState','ActiveState','SubState','MainPID','ControlGroup','InvocationID',
  'Restart','UnitFileState','TriggeredBy','FragmentPath','Type','KillMode','DropInPaths','NeedDaemonReload','Delegate'];
interface Stamp {pid: number; startTicks: string; cgroup: string}
export interface LegacySystemdCustodyWitness {
  schema: 'legacy-systemd-custody-v1'; unit: string; invocationId: string; bootId: string;
  cgroup: string; unitDigest: string; killMode: 'process' | 'control-group'; mainPid: number; processes: Stamp[];
}
/** Trusted guard captures original via capture() BEFORE its scoped inhibition.
 * This is a specific, reviewed transition, never permission to accept arbitrary
 * masked units. Original unit bytes and current effective mask are separate. */
export interface LegacySystemdInhibitionProfile {
  original: LegacySystemdCustodyWitness;
  mask: {path:string;dev:string;ino:string;uid:number;target:'/dev/null'};
  assertHeld(): Promise<void>;
}
const groupPath = (group: string) => join('/sys/fs/cgroup', group);
async function processStamp(pid: number): Promise<Stamp & {ppid: number}> {
  const stat = await readFile(`/proc/${pid}/stat`, 'utf8');
  const fields = stat.slice(stat.lastIndexOf(')') + 2).trim().split(/\s+/);
  const cgroup = (await readFile(`/proc/${pid}/cgroup`, 'utf8')).trim().split('\n').find(line=>line.startsWith('0::'))?.slice(3);
  if (!cgroup || normalize(cgroup) !== cgroup || !/^\d+$/.test(fields[19])) throw Error('maintenance_process_identity_unconfirmed');
  return {pid, ppid:Number(fields[1]), startTicks:fields[19], cgroup};
}
async function groupMembers(path: string): Promise<number[]> {
  const direct = (await readFile(join(path, 'cgroup.procs'), 'utf8')).trim();
  const ids = direct ? direct.split(/\s+/).map(Number) : [];
  for (const entry of await readdir(path, {withFileTypes:true})) {
    if (entry.isSymbolicLink()) throw Error('maintenance_cgroup_link');
    if (entry.isDirectory()) {
      try { ids.push(...await groupMembers(join(path, entry.name))); }
      catch(error) { if((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    }
  }
  if (ids.some(pid=>!Number.isSafeInteger(pid) || pid < 1)) throw Error('maintenance_cgroup_invalid');
  return [...new Set(ids)].sort((a,b)=>a-b);
}
async function assertFrozen(path: string) {
  const events = await readFile(join(path, 'cgroup.events'), 'utf8');
  if (!/^frozen 1$/m.test(events)) throw Error('maintenance_cgroup_not_frozen');
}
async function unitBytes(path: string) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await file.stat();
    if (!before.isFile() || before.size > 65536) throw Error('maintenance_unit_invalid');
    const bytes = await file.readFile(), after = await file.stat();
    if (bytes.length !== before.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs)
      throw Error('maintenance_unit_changed');
    return createHash('sha256').update(bytes).digest('hex');
  } finally { await file.close(); }
}
/** Read-only Linux custody adapter for an explicitly frozen legacy service.
 * Unlike the graceful handoff adapter, it accepts a transient KillMode=process
 * unit but binds EVERY existing member using a pidfd. It never calls freeze,
 * stop, kill, start, adopts a PID, or assumes MainPID exit killed descendants.
 *
 * A reviewed external operator must keep the service frozen until whole-group
 * termination and exclude other starts/writers throughout this operation. This
 * is an interruption boundary, not evidence remote work stopped. Frozen state
 * is essential: a live recursive /proc census cannot prove custody of children
 * racing the scan. A graceful thaw/stop is not this protocol.
 *
 * Limits: proves this invocation plus visible descendants and explicitly bound
 * writer PIDs; cannot discover previously detached writers with no retained
 * ownership. Installation qualification must establish that no such writers
 * exist. Refuse if that coverage cannot be established. */
export function createLinuxLegacyMaintenanceCustody(options: {
  unit: string; python: string;
  /** Authoritative service PID and required local writers, captured by the
   * reviewed installation adapter before freeze. Not caller-supplied UI data. */
  expectedMainPid: number;
  requiredProcesses: {pid: number; startTicks: string}[];
  /** External maintenance exclusion assertion. Must remain held while stopped
   * state is inspected and until the retained restart permit is consumed. */
  assertExclusion(): Promise<void>;
  inhibition?: LegacySystemdInhibitionProfile;
}) {
  if (process.platform !== 'linux') throw Error('maintenance_requires_linux');
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,150}\.service$/.test(options.unit) || !options.python.startsWith('/'))
    throw Error('maintenance_unit_invalid');
  if (!Number.isSafeInteger(options.expectedMainPid) || options.expectedMainPid < 1 ||
      !Array.isArray(options.requiredProcesses) || !options.requiredProcesses.length ||
      options.requiredProcesses.length > 4096 || new Set(options.requiredProcesses.map(p=>p.pid)).size !== options.requiredProcesses.length ||
      !options.requiredProcesses.some(p=>p.pid === options.expectedMainPid) ||
      options.requiredProcesses.some(p=>!Number.isSafeInteger(p.pid) || p.pid < 1 || !/^\d+$/.test(p.startTicks)))
    throw Error('maintenance_process_identity_unconfirmed');
  const required = structuredClone(options.requiredProcesses), unit = options.unit;
  const inhibition=options.inhibition&&{original:structuredClone(options.inhibition.original),
    mask:structuredClone(options.inhibition.mask),assertHeld:options.inhibition.assertHeld};
  if(inhibition){
    const o=inhibition.original,m=inhibition.mask;
    if(o.schema!=='legacy-systemd-custody-v1'||o.unit!==unit||o.mainPid!==options.expectedMainPid||
       !/^[a-f0-9]{64}$/.test(o.unitDigest)||!/^[a-f0-9]{32}$/.test(o.invocationId)||
       !/^[a-f0-9-]{36}$/.test(o.bootId)||!['process','control-group'].includes(o.killMode)||
       !o.cgroup.startsWith('/')||o.cgroup==='/'||normalize(o.cgroup)!==o.cgroup||
       m.uid!==process.getuid?.()||m.path!==`/run/user/${m.uid}/systemd/user.control/${unit}`||
       m.target!=='/dev/null'||!/^\d+$/.test(m.dev)||!/^\d+$/.test(m.ino))
      throw Error('maintenance_inhibition_profile_invalid');
  }
  async function assertMask(){
    if(!inhibition)return;
    await inhibition.assertHeld();
    const m=inhibition.mask,s=await lstat(m.path,{bigint:true});
    if(!s.isSymbolicLink()||s.dev.toString()!==m.dev||s.ino.toString()!==m.ino||
       s.uid!==BigInt(m.uid)||await readlink(m.path)!==m.target||await realpath(dirname(m.path))!==dirname(m.path))
      throw Error('maintenance_inhibition_changed');
  }
  async function show(): Promise<Record<string,string>> {
    await assertMask();
    const {stdout} = await execute('systemctl', ['--user','show',...properties.map(p=>'--property='+p),'--',unit], {maxBuffer:65536});
    const v = Object.fromEntries(stdout.trim().split('\n').map(line=>{const i=line.indexOf('='); return [line.slice(0,i),line.slice(i+1)];}));
    if (v.Id !== unit || v.Restart !== 'no' || v.TriggeredBy ||
        !(inhibition?['','simple']:['simple']).includes(v.Type) ||
        !['process','control-group'].includes(v.KillMode) || v.Delegate !== 'no' || v.DropInPaths || v.NeedDaemonReload !== 'no')
      throw Error('maintenance_unit_policy_unqualified');
    if(inhibition){
      if(v.LoadState!=='masked'||!['masked','masked-runtime'].includes(v.UnitFileState)||v.FragmentPath!==inhibition.mask.path||
         (v.InvocationID&&v.InvocationID!==inhibition.original.invocationId))throw Error('maintenance_inhibition_unconfirmed');
      await assertMask();
      // This digest always names the pre-mask unit bytes, retained separately
      // from the exact current mask witness in witnessDigest below.
      return {...v,unitDigest:inhibition.original.unitDigest};
    }
    if(v.LoadState!=='loaded'||!['transient','disabled'].includes(v.UnitFileState))throw Error('maintenance_unit_policy_unqualified');
    return {...v, unitDigest:await unitBytes(v.FragmentPath)};
  }
  async function capture(): Promise<LegacySystemdCustodyWitness> {
    await options.assertExclusion();
    const s = await show(), group = s.ControlGroup;
    if (s.ActiveState !== 'active' || !['running','frozen'].includes(s.SubState) ||
        Number(s.MainPID) !== options.expectedMainPid || !/^[a-f0-9]{32}$/.test(s.InvocationID) ||
        !group || group === '/' || !group.startsWith('/') || normalize(group) !== group) throw Error('maintenance_unit_identity_changed');
    await assertFrozen(groupPath(group));
    const members = await groupMembers(groupPath(group));
    if (!members.includes(options.expectedMainPid) || members.length > 4096) throw Error('maintenance_census_invalid');
    const processes = await Promise.all(members.map(async pid=>{const {ppid, ...stamp}=await processStamp(pid); return stamp;}));
    if (processes.some(p=>p.cgroup !== group && !p.cgroup.startsWith(group+'/')) ||
        required.some(r=>!processes.some(p=>p.pid===r.pid && p.startTicks===r.startTicks))) throw Error('maintenance_writer_outside_custody');
    // Reject a visible child of ANY cgroup member outside the owned subtree.
    // This does not invent coverage of unrecorded historic detached processes.
    const parents = new Map<number,number>();
    for (const entry of await readdir('/proc')) if (/^\d+$/.test(entry)) {
      try { const raw=await readFile(`/proc/${entry}/stat`,'utf8'); parents.set(Number(entry),Number(raw.slice(raw.lastIndexOf(')')+2).split(/\s+/)[1])); }
      catch (error) { if (!['ENOENT','ESRCH'].includes(String((error as NodeJS.ErrnoException).code))) throw error; }
    }
    const descendants = new Set(members); let changed = true;
    while (changed) { changed=false; for(const [pid,parent] of parents) if(descendants.has(parent) && !descendants.has(pid)){descendants.add(pid);changed=true;} }
    if ([...descendants].some(pid=>!members.includes(pid))) throw Error('maintenance_writer_outside_custody');
    await assertFrozen(groupPath(group)); await options.assertExclusion();
    const again=await show();
    if (legacyRecoveryDigest(s)!==legacyRecoveryDigest(again) || legacyRecoveryDigest(members)!==legacyRecoveryDigest(await groupMembers(groupPath(group))))
      throw Error('maintenance_custody_changed');
    const witness:LegacySystemdCustodyWitness={schema:'legacy-systemd-custody-v1',unit,invocationId:s.InvocationID,
      bootId:(await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim(), cgroup:group,
      // Reloading a masked transient unit resets displayed Type/KillMode to
      // defaults. Preserve original policy provenance, not those new defaults.
      unitDigest:s.unitDigest,killMode:inhibition?.original.killMode??s.KillMode as 'process'|'control-group',mainPid:options.expectedMainPid,processes};
    if(inhibition&&legacyRecoveryDigest(witness)!==legacyRecoveryDigest(inhibition.original))
      throw Error('maintenance_original_unit_changed');
    return witness;
  }
  return {capture, async bind(): Promise<LegacyMaintenanceCustody & {witness:LegacySystemdCustodyWitness}> {
    const witness = await capture();
    // The helper remains OUTSIDE the frozen unit and holds kernel identities
    // until every original member exits. Lost helper/FD state is never repaired
    // using PID absence or an inactive service status.
    const program = String.raw`import os,sys,json,select
w=json.loads(sys.argv[1])
assert open('/proc/sys/kernel/random/boot_id').read().strip()==w['bootId']
poll=select.poll();fds=[]
for p in w['processes']:
 def stamp():
  raw=open('/proc/%d/stat'%p['pid']).read();fields=raw[raw.rfind(')')+2:].split()
  group=next(x[3:] for x in open('/proc/%d/cgroup'%p['pid']).read().splitlines() if x.startswith('0::'))
  return fields[19],group
 assert stamp()==(p['startTicks'],p['cgroup'])
 fd=os.pidfd_open(p['pid'],0)
 assert stamp()==(p['startTicks'],p['cgroup'])
 fds.append(fd);poll.register(fd,select.POLLIN)
print('bound',flush=True)
remaining=set(fds)
while remaining:
 for fd,event in poll.poll():
  assert event&(select.POLLIN|select.POLLHUP)
  poll.unregister(fd);remaining.remove(fd)
print('exited',flush=True)
for fd in fds:os.close(fd)
`;
    const child=spawn(options.python,['-I','-u','-c',program,JSON.stringify(witness)],{stdio:['ignore','pipe','ignore']});
    let bound=false, exitedSeen=false, exitedConfirmed=false, closed=false, failed=false, text='';
    let readyResolve!:()=>void,readyReject!:(e:Error)=>void,exitResolve!:()=>void,exitReject!:(e:Error)=>void;
    const ready=new Promise<void>((a,b)=>{readyResolve=a;readyReject=b;});
    const exited=new Promise<void>((a,b)=>{exitResolve=a;exitReject=b;});exited.catch(()=>{});
    const fail=()=>{failed=true;readyReject(Error('maintenance_pidfd_unconfirmed'));exitReject(Error('maintenance_pidfd_unconfirmed'));};
    child.on('error',fail);
    child.stdout.on('data',chunk=>{
      text+=String(chunk);if(text.length>128){child.kill('SIGTERM');fail();return;}
      while(text.includes('\n')){const i=text.indexOf('\n'),line=text.slice(0,i);text=text.slice(i+1);
        if(line==='bound'&&!bound){bound=true;readyResolve();}else if(line==='exited'&&bound&&!exitedSeen){exitedSeen=true;}else fail();}
    });
    child.on('close',code=>{if(code===0&&bound&&exitedSeen&&!closed&&!failed){exitedConfirmed=true;exitResolve();}else fail();});
    try { await ready; if(legacyRecoveryDigest(witness)!==legacyRecoveryDigest(await capture()))throw Error('maintenance_custody_changed'); }
    catch(error){child.kill('SIGTERM');throw error;}
    return {witness,witnessDigest:legacyRecoveryDigest(inhibition?{original:witness,mask:inhibition.mask}:witness),exited,
      async confirmExited(){
        if(closed||!exitedConfirmed)throw Error('maintenance_pidfd_exit_unconfirmed');
        await options.assertExclusion();
        const s=await show();
        if(s.unitDigest!==witness.unitDigest || s.MainPID!=='0' || !['inactive','failed'].includes(s.ActiveState) ||
            !['dead','failed'].includes(s.SubState) || (s.InvocationID&&s.InvocationID!==witness.invocationId) ||
            (!inhibition&&s.KillMode!==witness.killMode) || (s.ControlGroup&&s.ControlGroup!==witness.cgroup) ||
            (await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim()!==witness.bootId)
          throw Error('maintenance_unit_exit_unconfirmed');
        try { if((await groupMembers(groupPath(witness.cgroup))).length)throw Error('maintenance_children_still_running'); }
        catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
        await options.assertExclusion();
        if(legacyRecoveryDigest(s)!==legacyRecoveryDigest(await show()))throw Error('maintenance_unit_exit_changed');
      },
      close(){if(closed)return;closed=true;if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');},
    };
  }};
}
