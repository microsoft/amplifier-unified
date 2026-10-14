import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {DatabaseSync} from 'node:sqlite';
import {dirname,join} from 'node:path';
import {randomUUID} from 'node:crypto';

/** One selected operation, no warm processes or automatic retries. */
export function createGitWorker({python='python3', directory, executionHost, timeout=360000,onMayBeIdle=()=>{}}={}) {
  const moduleDirectory=fileURLToPath(new URL('../python/',import.meta.url));
  const children=new Set(); let closed=false;
  async function request(method,args) {
    if(closed||children.size>=8)throw Error('Worktree operation capacity reached; request was not sent.');
    const workerId=method==='get'?undefined:randomUUID(),gateDirectory=dirname(directory);
    if(workerId){const gate=new DatabaseSync(join(gateDirectory,'worktree-quiescence.sqlite'),{readOnly:true});try{if(gate.prepare('SELECT 1 FROM fence WHERE id=1').get())throw Object.assign(Error('Worktree intake closed; no worker admitted'),{executed:false});}finally{gate.close()}}
    const input=JSON.stringify({directory,executionHost,method,args,workerId,gateDirectory});
    if(Buffer.byteLength(input)>65536)throw Error('Worktree request exceeds 64 KiB.');
    if(workerId){const ledger=new DatabaseSync(join(gateDirectory,'worktree-workers.sqlite'));try{ledger.prepare("INSERT INTO workers VALUES(?,'pending')").run(workerId)}finally{ledger.close()}}
    const notStarted=()=>{if(workerId){const ledger=new DatabaseSync(join(gateDirectory,'worktree-workers.sqlite'));try{ledger.prepare("UPDATE workers SET state='settled' WHERE id=?").run(workerId)}finally{ledger.close()}}};
    return new Promise((resolve,reject)=>{
      let child;try{child=spawn(python,['-m','amplifier_unified_worktrees.worker'],{stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONPATH:moduleDirectory}})}catch(error){notStarted();reject(Object.assign(error,{executed:false}));return;}
      children.add(child);let chunks=[],bytes=0,problem;
      const stop=message=>{if(problem)return;problem=Error(message);reject(problem);};
      const timer=setTimeout(()=>stop('Worktree operation timed out; inspect its durable receipt. No effect was replayed.'),timeout);
      child.stdout.on('data',chunk=>{bytes+=chunk.length;if(bytes>1024*1024)stop('Worktree response exceeded 1 MiB; outcome may be unknown.');else chunks.push(chunk)});
      child.stderr.on('data',()=>{});
      child.stdin.on('error',()=>{problem=Error('Worktree input transport failed; outcome may be unknown.')});
      child.once('error',error=>{problem=error;if(!child.pid){notStarted();problem.executed=false;}});
      child.once('close',code=>{clearTimeout(timer);children.delete(child);try{Promise.resolve(onMayBeIdle()).catch(()=>{})}catch{}if(problem||code!==0){reject(problem||Error('Worktree worker ended; inspect the receipt before any new action.'));return;}
        try{const value=JSON.parse(Buffer.concat(chunks).toString());if(value.error)reject(Object.assign(Error(value.error),{executed:value.executed,record:value.record}));else resolve(value.result)}catch(error){reject(error)}
      });child.stdin.end(input);
    });
  }
  return {quiescenceCoverage:1,request,async close(){closed=true;await Promise.all([...children].map(child=>new Promise(resolve=>child.once('close',resolve))))}};
}
