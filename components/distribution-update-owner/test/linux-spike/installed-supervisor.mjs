// Disposable test process around the same production factory called by the
// packaged full-owner installer. No independent lifecycle or proof adapter.
import {readFile} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {runProductionSupervisor} from '@amplifier/unified-distribution-update-owner';
const config=JSON.parse(await readFile(process.argv[2],'utf8'));
const supervisor=await runProductionSupervisor(config,{startInitial:process.argv[3]==='initial',
 resolveSources:async()=>await(await fetch(new URL('/sources',config.release.channelUrl))).json()});
process.stdout.write(JSON.stringify({ready:true})+'\n');
for await(const line of createInterface({input:process.stdin})){
 const {id,action,args={}}=JSON.parse(line);
 void(async()=>{
  if(action==='check')return supervisor.owner.check(args.commandId);
  if(action==='install')return supervisor.owner.install(args.commandId);
  if(action==='receipt')return supervisor.owner.receipt(args.commandId);
  if(action==='reconcile')return supervisor.owner.reconcile(args.commandId);
  if(action==='service-inspect')return supervisor.service.inspect();
  if(action==='stop')return supervisor.stopService(args.commandId);
  if(action==='close'){await supervisor.close();return true;}
  throw Error('unknown_fixture_action');
 })().then(result=>process.stdout.write(JSON.stringify({id,result})+'\n'),error=>process.stdout.write(JSON.stringify({id,error:String(error)})+'\n'));
}
