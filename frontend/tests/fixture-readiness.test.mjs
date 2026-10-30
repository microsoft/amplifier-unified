import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {startFixture} from './fixture-readiness.mjs';

const posix={skip:process.platform==='win32'?'Graceful SIGTERM is a POSIX fixture contract':false};
const receipt="{fixture:'sample-ui',pid:process.pid,base:'http://127.0.0.1:12345'}";

// Synthetic children exercise the actual pipe, identity and shutdown boundary.
async function child(t,body,options={}) {
 const directory=await mkdtemp(join(tmpdir(),'fixture-readiness-'));
 let running;
 t.after(async()=>{
  try{await running?.stop()}
  finally{await rm(directory,{recursive:true,force:true})}
 });
 const stopped=join(directory,'stopped');
 const script=`
  import {writeFileSync} from 'node:fs';
  process.on('SIGTERM',()=>{writeFileSync(${JSON.stringify(stopped)},'stopped');process.exit(0)});
  setInterval(()=>{},1000);
  ${body}
 `;
 return {
  started:startFixture({command:process.execPath,args:['--input-type=module','-e',script],
   env:process.env,tag:'sample-ui',name:'Sample',...options}).then(result=>{running=result;return result}),
  stopped:async()=>assert.equal(await readFile(stopped,'utf8'),'stopped'),
 };
}

test('only the owned receipt read across chunks completes readiness',posix,async t=>{
 const {started,stopped}=await child(t,`
  console.log('ordinary startup log');
  console.log('null');console.log('[]');console.log('"not a receipt"');
  console.log(JSON.stringify({...${receipt},fixture:'other-ui',pid:0}));
  const ready=JSON.stringify({...${receipt},base:process.env.FIXTURE_BASE});
  process.stdout.write(ready.slice(0,20));
  setTimeout(()=>process.stdout.write(ready.slice(20)+'\\n'),20);
 `,{env:{...process.env,FIXTURE_BASE:'http://127.0.0.1:23456'}});
 const {fixture,base,stop}=await started;
 try {
  assert.equal(base,'http://127.0.0.1:23456');
  assert.equal(fixture.stdout.listenerCount('data'),0);
  assert.equal(fixture.listenerCount('exit'),0);
  // ownFixture's error observer remains after readiness removes its own.
  assert.equal(fixture.listenerCount('error'),1);
 } finally {
  const first=stop();assert.equal(stop(),first);
  assert.deepEqual(await first,{pid:fixture.pid,code:0,signal:null});
 }
 await stopped();
});

test('a matching tag still requires the exact child PID and loopback address',posix,async t=>{
 for(const [name,change] of [
  ['different PID','pid:process.pid+1'],
  ['string PID','pid:String(process.pid)'],
  ['other host',"base:'http://localhost:12345'"],
  ['remote host',"base:'http://192.0.2.1:12345'"],
  ['wrong scheme',"base:'https://127.0.0.1:12345'"],
  ['zero port',"base:'http://127.0.0.1:0'"],
  ['URL path',"base:'http://127.0.0.1:12345/unrelated'"],
 ]) await t.test(name,async t=>{
  const {started,stopped}=await child(t,`console.log(JSON.stringify({...${receipt},${change}}));`);
  await assert.rejects(started,/^Error: Invalid sample fixture readiness receipt$/);
  await stopped();
 });
});

test('an unrelated receipt does not satisfy readiness and timeout stops the child',posix,async t=>{
 const {started,stopped}=await child(t,`
  process.stderr.write('waiting for owned receipt');
  console.log(JSON.stringify({...${receipt},fixture:'other-ui'}));
 `,{timeoutMs:500});
 await assert.rejects(started,/Sample fixture did not become ready: waiting for owned receipt/);
 await stopped();
});

test('the incomplete-line bound rejects excess output and stops the child',posix,async t=>{
 const {started,stopped}=await child(t,"process.stdout.write('x'.repeat(8001));");
 await assert.rejects(started,/Sample fixture readiness output exceeded its bound/);
 await stopped();
});

test('an incomplete line at the bound can finish before an owned receipt',posix,async t=>{
 const {started,stopped}=await child(t,`
  process.stdout.write('x'.repeat(8000));
  setTimeout(()=>console.log('\\n'+JSON.stringify(${receipt})),20);
 `);
 const {base,stop}=await started;
 try{assert.equal(base,'http://127.0.0.1:12345')}
 finally{await stop()}
 await stopped();
});

test('early exit retains bounded diagnostics and reports cleanup without replacing the error',async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const {started}=await child(t,`
  process.stderr.write('discarded-prefix'+'x'.repeat(8000),()=>process.exit(7));
 `);
 await assert.rejects(started,error=>{
  assert.equal(error.message,'Sample fixture exited before readiness (7): '+'x'.repeat(8000));
  return true;
 });
 assert.equal(reports.length,1);
 assert.equal(reports[0][0],'Sample startup cleanup failed:');
 assert.match(reports[0][1].message,/Sample fixture shutdown failed \(7\)/);
});

test('signal cancellation before readiness remains a failure',posix,async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const {started}=await child(t,"process.removeAllListeners('SIGTERM');process.kill(process.pid,'SIGTERM');");
 await assert.rejects(started,/Sample fixture exited before readiness \(SIGTERM\)/);
 assert.equal(reports.length,1);
 assert.match(reports[0][1].message,/Sample fixture shutdown failed \(SIGTERM\)/);
});

test('spawn errors are propagated even when cleanup reports the same failure',async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const directory=await mkdtemp(join(tmpdir(),'fixture-readiness-missing-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 let original;
 await assert.rejects(startFixture({command:join(directory,'missing'),args:[],env:process.env,
  tag:'sample-ui',name:'Sample'}),error=>{original=error;return error.code==='ENOENT'});
 assert.equal(reports.length,1);
 assert.equal(reports[0][1],original);
});
