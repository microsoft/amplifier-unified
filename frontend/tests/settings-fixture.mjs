import {ownFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

// Read readiness from our own child, never from a possibly unrelated listener.
export async function startSettingsFixture(env={}) {
 const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
  ['-u',fileURLToPath(new URL('../../tests/fixtures/settings_ui_server.py',import.meta.url))],
  {stdio:['ignore','pipe','pipe'],env:{...process.env,...env}});
 const stop=ownFixture(fixture,{label:"Settings fixture"});
 let stderr='';
 fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
 try {
  const base=await new Promise((resolve,reject)=>{
   let pending='';
   const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
   const failed=error=>{cleanup();reject(error)};
   const exited=(code,signal)=>failed(new Error(`Settings fixture exited before readiness (${code??signal}): ${stderr}`));
   const data=chunk=>{
    pending+=chunk;
    for(let end;(end=pending.indexOf('\n'))>=0;){
     const line=pending.slice(0,end);pending=pending.slice(end+1);
     let ready;try{ready=JSON.parse(line)}catch{continue}
     if(ready.fixture!=='settings-ui')continue;
     if(ready.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(ready.base)){
      failed(new Error('Invalid settings fixture readiness receipt'));return;
     }
     cleanup();resolve(ready.base);return;
    }
    if(pending.length>8000)failed(new Error('Settings fixture readiness output exceeded its bound'));
   };
   const timer=setTimeout(()=>failed(new Error(`Settings fixture did not become ready: ${stderr}`)),30000);
   fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
  });
  return {fixture,base,stop};
 } catch(error) {
  try{await stop()}catch(cleanupError){console.error('Settings startup cleanup failed:',cleanupError)}
  throw error;
 }
}
