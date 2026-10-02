import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

/** One selected operation, no warm processes or automatic retries. */
export function createGitWorker({python='python3', directory, executionHost, timeout=360000}={}) {
  const moduleDirectory=fileURLToPath(new URL('../python/',import.meta.url));
  const children=new Set(); let closed=false;
  async function request(method,args) {
    if(closed||children.size>=8)throw Error('Worktree operation capacity reached; request was not sent.');
    const input=JSON.stringify({directory,executionHost,method,args});
    if(Buffer.byteLength(input)>65536)throw Error('Worktree request exceeds 64 KiB.');
    return new Promise((resolve,reject)=>{
      const child=spawn(python,['-m','amplifier_unified_worktrees.worker'],{stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONPATH:moduleDirectory}});
      children.add(child);let chunks=[],bytes=0,problem;
      const stop=message=>{problem=Error(message);child.kill('SIGKILL');};
      const timer=setTimeout(()=>stop('Worktree operation timed out; inspect its durable receipt. No effect was replayed.'),timeout);
      child.stdout.on('data',chunk=>{bytes+=chunk.length;if(bytes>1024*1024)stop('Worktree response exceeded 1 MiB; outcome may be unknown.');else chunks.push(chunk)});
      child.stderr.on('data',()=>{});
      child.stdin.on('error',()=>{problem=Error('Worktree input transport failed; outcome may be unknown.')});
      child.once('error',error=>{problem=error});
      child.once('close',code=>{clearTimeout(timer);children.delete(child);if(problem||code!==0){reject(problem||Error('Worktree worker ended; inspect the receipt before any new action.'));return;}
        try{const value=JSON.parse(Buffer.concat(chunks).toString());if(value.error)reject(Object.assign(Error(value.error),{executed:value.executed,record:value.record}));else resolve(value.result)}catch(error){reject(error)}
      });child.stdin.end(input);
    });
  }
  return {request,async close(){closed=true;await Promise.all([...children].map(child=>new Promise(resolve=>child.once('close',resolve))))}};
}
