import {spawn} from 'node:child_process';
import {ownFixture} from './fixture-lifecycle.mjs';

// Read readiness from our own child, never from a possibly unrelated listener.
// ownFixture remains responsible for shutdown, including failed startup.
export async function startFixture({command,args,env,tag,name,timeoutMs=30000}) {
 const fixture=spawn(command,args,{stdio:['ignore','pipe','pipe'],env});
 const stop=ownFixture(fixture,{label:`${name} fixture`});
 let stderr='';
 fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
 try {
  const base=await new Promise((resolve,reject)=>{
   let pending='';
   const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
   const failed=error=>{cleanup();reject(error)};
   const exited=(code,signal)=>failed(new Error(`${name} fixture exited before readiness (${code??signal}): ${stderr}`));
   const data=chunk=>{
    pending+=chunk;
    for(let end;(end=pending.indexOf('\n'))>=0;){
     const line=pending.slice(0,end);pending=pending.slice(end+1);
     let ready;try{ready=JSON.parse(line)}catch{continue}
     if(ready?.fixture!==tag)continue;
     if(ready.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(ready.base)){
      failed(new Error(`Invalid ${name.toLowerCase()} fixture readiness receipt`));return;
     }
     cleanup();resolve(ready.base);return;
    }
    if(pending.length>8000)failed(new Error(`${name} fixture readiness output exceeded its bound`));
   };
   const timer=setTimeout(()=>failed(new Error(`${name} fixture did not become ready: ${stderr}`)),timeoutMs);
   fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
  });
  return {fixture,base,stop};
 } catch(error) {
  try{await stop()}catch(cleanupError){console.error(`${name} startup cleanup failed:`,cleanupError)}
  throw error;
 }
}
