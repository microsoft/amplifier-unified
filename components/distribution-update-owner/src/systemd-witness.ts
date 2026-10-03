import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join, normalize} from 'node:path';
import {open} from 'node:fs/promises';
import {constants} from 'node:fs';
const execute = promisify(execFile);
const properties=['Id','LoadState','ActiveState','SubState','MainPID','ControlGroup','InvocationID','Restart','UnitFileState','TriggeredBy','FragmentPath','Type','KillMode','DropInPaths','NeedDaemonReload'];
export interface SystemdSourceWitness {
  unit: string; pid: number; bootId: string; startTicks: string;
  invocationId: string; cgroup: string; unitDigest: string;
}
export interface BoundSystemdExit {
  witness: SystemdSourceWitness;
  /** Kernel-held process identity, not polling an inactive unit or numeric PID. */
  exited: Promise<void>;
  /** Closes only the owned observer helper; never signals the service. */
  close(): void;
}
export interface SystemdSourceObserver {
  capture(): Promise<SystemdSourceWitness>;
  bind(witness: SystemdSourceWitness): Promise<BoundSystemdExit>;
  confirmExited(witness: SystemdSourceWitness): Promise<void>;
}
const digest=(b:Buffer|string)=>createHash('sha256').update(b).digest('hex');
export function witnessDigest(w:SystemdSourceWitness){return digest(JSON.stringify(w));}
function unitName(unit:string){if(!/^[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,150}\.service$/.test(unit))throw Error('manual_unit_invalid');return unit;}
/** Linux read-only systemd authority plus a retained pidfd. No stop/start/enable,
 * kill or process adoption is available through this adapter. */
export function createLinuxSystemdSourceObserver(options:{unit:string;python:string}):SystemdSourceObserver {
  if(process.platform!=='linux')throw Error('manual_systemd_requires_linux');
  const unit=unitName(options.unit);
  if(!options.python.startsWith('/'))throw Error('manual_witness_python_required');
  async function show():Promise<Record<string,string>>{
    const {stdout}=await execute('systemctl',['--user','show',...properties.map(p=>'--property='+p),'--',unit],{maxBuffer:65536});
    const values=Object.fromEntries(stdout.trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)];}));
    if(values.Id!==unit||values.LoadState!=='loaded'||values.Restart!=='no'||values.UnitFileState!=='disabled'||values.TriggeredBy||values.Type!=='simple'||values.KillMode!=='control-group'||values.DropInPaths||values.NeedDaemonReload!=='no')
      throw Error('manual_systemd_policy_unqualified');
    const file=await open(values.FragmentPath,constants.O_RDONLY|constants.O_NOFOLLOW);
    try{const s=await file.stat();if(!s.isFile()||s.size>65536)throw Error('manual_unit_unqualified');
      const b=Buffer.alloc(s.size+1);let n=0;while(n<b.length){const r=await file.read(b,n,b.length-n,null);if(!r.bytesRead)break;n+=r.bytesRead;}
      const a=await file.stat();if(n!==s.size||s.mtimeMs!==a.mtimeMs||s.ctimeMs!==a.ctimeMs)throw Error('manual_unit_changed');
      return {...values,unitDigest:digest(b.subarray(0,n))};
    }finally{await file.close();}
  }
  async function processStamp(pid:number){
    const raw=await readFile(`/proc/${pid}/stat`,'utf8'),fields=raw.slice(raw.lastIndexOf(')')+2).trim().split(/\s+/);
    return {bootId:(await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim(),startTicks:fields[19],
      cgroup:(await readFile(`/proc/${pid}/cgroup`,'utf8')).trim().split('\n').find(s=>s.startsWith('0::'))?.slice(3)};
  }
  return {
    async capture(){
      const s=await show(),pid=Number(s.MainPID);
      if(s.ActiveState!=='active'||s.SubState!=='running'||!Number.isSafeInteger(pid)||pid<1||!/^[a-f0-9]{32}$/.test(s.InvocationID))throw Error('manual_systemd_not_running');
      const p=await processStamp(pid);
      if(!s.ControlGroup||p.cgroup!==s.ControlGroup||normalize(s.ControlGroup)!==s.ControlGroup||!s.ControlGroup.startsWith('/'))throw Error('manual_cgroup_unqualified');
      const again=await show();if(again.MainPID!==s.MainPID||again.InvocationID!==s.InvocationID||again.unitDigest!==s.unitDigest)throw Error('manual_process_changed');
      return {unit,pid,bootId:p.bootId,startTicks:p.startTicks,invocationId:s.InvocationID,cgroup:s.ControlGroup,unitDigest:s.unitDigest};
    },
    async bind(witness){
      if(witnessDigest(await this.capture())!==witnessDigest(witness))throw Error('manual_process_changed');
      // Open pidfd before asking for a stop. The helper verifies boot/starttime
      // and cgroup on both sides of pidfd_open and keeps the FD until exit.
      const program=String.raw`import os,sys,json,select
w=json.loads(sys.argv[1])
def stamp():
 raw=open('/proc/%d/stat'%w['pid']).read();fields=raw[raw.rfind(')')+2:].split()
 group=next(x[3:] for x in open('/proc/%d/cgroup'%w['pid']).read().splitlines() if x.startswith('0::'))
 return open('/proc/sys/kernel/random/boot_id').read().strip(),fields[19],group
expected=(w['bootId'],w['startTicks'],w['cgroup'])
assert stamp()==expected
fd=os.pidfd_open(w['pid'],0)
assert stamp()==expected
poll=select.poll();poll.register(fd,select.POLLIN)
print('bound',flush=True)
assert poll.poll()[0][1]&(select.POLLIN|select.POLLHUP)
print('exited',flush=True)
os.close(fd)
`;
      const child=spawn(options.python,['-I','-u','-c',program,JSON.stringify(witness)],{stdio:['ignore','pipe','ignore']});
      let text='',bound=false,exitSeen=false,closed=false;
      let boundResolve!:()=>void,boundReject!:(e:Error)=>void,exitResolve!:()=>void,exitReject!:(e:Error)=>void;
      const ready=new Promise<void>((a,b)=>{boundResolve=a;boundReject=b;}),exited=new Promise<void>((a,b)=>{exitResolve=a;exitReject=b;});
      exited.catch(()=>{});
      const failure=()=>{boundReject(Error('manual_pidfd_unconfirmed'));exitReject(Error('manual_pidfd_unconfirmed'));};
      child.on('error',failure);child.stdout.on('data',chunk=>{text+=String(chunk);if(text.length>128){child.kill('SIGTERM');return failure();}
        while(text.includes('\n')){const i=text.indexOf('\n'),line=text.slice(0,i);text=text.slice(i+1);if(line==='bound'&&!bound){bound=true;boundResolve();}else if(line==='exited'&&bound){exitSeen=true;}else failure();}});
      child.on('close',code=>{if(code===0&&bound&&exitSeen)exitResolve();else failure();});
      try{await ready;}catch(error){child.kill('SIGTERM');throw error;}
      return {witness,exited,close(){if(closed)return;closed=true;if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');}};
    },
    async confirmExited(witness){
      const s=await show();
      if(s.unitDigest!==witness.unitDigest||s.MainPID!=='0'||s.ActiveState!=='inactive'||s.SubState!=='dead'||
        (s.InvocationID&&s.InvocationID!==witness.invocationId))throw Error('manual_unit_exit_unconfirmed');
      if((await readFile('/proc/sys/kernel/random/boot_id','utf8')).trim()!==witness.bootId)throw Error('manual_boot_changed');
      // Empty or kernel-removed original cgroup only qualifies AFTER pidfd exit
      // and authenticated durable source closure, never by itself.
      const path=join('/sys/fs/cgroup',witness.cgroup);
      try{if((await readFile(join(path,'cgroup.procs'),'utf8')).trim())throw Error('manual_children_still_running');}
      catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
    },
  };
}
