import {spawn} from 'node:child_process';
import {tmpdir} from 'node:os';

function failure(code){return Object.assign(Error(code),{code});}
function source(value){
 if(!value||typeof value.repository!=='string'||value.repository.length>2048||typeof value.ref!=='string'||!value.ref||value.ref.length>200)throw failure('source_configuration_invalid');
 let url;try{url=new URL(value.repository);}catch{throw failure('source_configuration_invalid');}
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.pathname==='/'||url.href!==value.repository)throw failure('source_configuration_invalid');
 const ref=value.ref.startsWith('refs/')?value.ref:'refs/heads/'+value.ref;
 if(!/^refs\/(heads|tags)\//.test(ref)||/[\x00-\x20\x7f~^:?*\[\\]/.test(ref)||ref.includes('..')||ref.includes('@{')||ref.endsWith('/')||ref.split('/').some(part=>!part||part.startsWith('.')||part.endsWith('.lock')||part.endsWith('.')))throw failure('source_ref_invalid');
 if(value.protected!==undefined&&typeof value.protected!=='boolean')throw failure('source_configuration_invalid');
 return {repository:url.href,ref:value.ref,qualifiedRef:ref,protected:value.protected===true,key:JSON.stringify([url.href,value.ref])};
}

/** Read-only Git, bounded output/lifetime, with no inherited environment capture. */
function gitRequest(command,args,{env,signal,timeoutMs}){
 if(signal.aborted)throw failure('source_observation_aborted');
 return new Promise((resolve,reject)=>{
  const environment={...process.env,...env,GIT_TERMINAL_PROMPT:'0',GCM_INTERACTIVE:'never'};
  for(const key of ['GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR'])delete environment[key];
  const child=spawn(command,args,{cwd:tmpdir(),env:environment,stdio:['ignore','pipe','pipe'],detached:true});
  let error,stdout='',bytes=0;
  const stop=code=>{error??=failure(code);if(child.pid){try{process.kill(-child.pid,'SIGKILL');}catch{child.kill('SIGKILL');}}};
  const abort=()=>stop('source_observation_aborted'),timer=setTimeout(()=>stop('source_observation_timeout'),timeoutMs);
  signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  child.stdout.on('data',chunk=>{bytes+=chunk.length;if(bytes>65536)stop('source_observation_output_limit');else stdout+=chunk.toString('utf8');});
  child.stderr.on('data',chunk=>{bytes+=chunk.length;if(bytes>65536)stop('source_observation_output_limit');});
  child.on('error',()=>{error??=failure('source_observation_unavailable');});
  child.on('close',code=>{clearTimeout(timer);signal.removeEventListener('abort',abort);if(error)reject(error);else if(code!==0)reject(failure('source_observation_unavailable'));else resolve(stdout.trim());});
 });
}

/** Adapter for Git-backed configured components. Registry sources need their own
 * source owner; an unknown source is never filled from publisher metadata. */
export function createGitSourceResolver({sources,git='git',env,timeoutMs=15000,deadlineMs=60000,concurrency=4}){
 if(process.platform==='win32')throw failure('source_process_ownership_unqualified');
 if(typeof git!=='string'||!git||git.includes('\0')||!Array.isArray(sources)||!sources.length||sources.length>500||!Number.isInteger(concurrency)||concurrency<1||concurrency>8||!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>60000||!Number.isInteger(deadlineMs)||deadlineMs<1||deadlineMs>300000)throw failure('source_configuration_invalid');
 const configured=new Map();for(const row of sources){const entry=source(row);if(configured.has(entry.key))throw failure('source_configuration_duplicate');configured.set(entry.key,entry);}
 return async(components,context)=>{
  if(!Array.isArray(components)||!components.length||components.length>500)throw failure('source_inventory_invalid');
  const selected=new Map();
  for(const component of components){const requested=source(component),entry=configured.get(requested.key);if(!entry)throw failure('source_not_configured');if(entry.protected)throw failure('source_preserved');selected.set(entry.key,entry);}
  const pending=[...selected.values()],results=new Array(pending.length),controller=new AbortController();
  const signal=AbortSignal.any([controller.signal,context?.signal??new AbortController().signal,AbortSignal.timeout(deadlineMs)]);
  let next=0,firstError;
  const run=args=>gitRequest(git,args,{env,signal,timeoutMs});
  const worker=async()=>{
   while(next<pending.length&&!signal.aborted){const index=next++,entry=pending[index];try{
    await run(['check-ref-format',entry.qualifiedRef]);
    // A local insteadOf rule is a source override, not evidence about this URL.
    if(await run(['ls-remote','--get-url',entry.repository])!==entry.repository)throw failure('source_override_preserved');
    const output=await run(['ls-remote','--exit-code','--quiet',entry.repository,entry.qualifiedRef]),refs=new Map();
    for(const line of output.split('\n')){const match=/^([a-f0-9]{40}|[a-f0-9]{64})\t(.+)$/.exec(line);if(!match||![entry.qualifiedRef,entry.qualifiedRef+'^{}'].includes(match[2])||refs.has(match[2]))throw failure('source_observation_invalid');refs.set(match[2],match[1]);}
    if(!refs.has(entry.qualifiedRef)||refs.has(entry.qualifiedRef+'^{}')&&!entry.qualifiedRef.startsWith('refs/tags/'))throw failure('source_observation_invalid');
    results[index]={repository:entry.repository,ref:entry.ref,revision:refs.get(entry.qualifiedRef+'^{}')??refs.get(entry.qualifiedRef),protected:false};
   }catch(error){firstError??=error;controller.abort();}}
  };
  await Promise.all(Array.from({length:Math.min(concurrency,pending.length)},worker));
  if(firstError)throw firstError;if(signal.aborted)throw failure('source_observation_aborted');return results;
 };
}
