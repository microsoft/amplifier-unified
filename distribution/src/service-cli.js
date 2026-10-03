#!/usr/bin/env node
import {attachSupervisorSignalHandlers,token,startupFailureFrom} from '@amplifier/unified-distribution-update-owner';
import {openInstalledService,connectInstalledService} from './service.js';

async function main(){
 const [command,...values]=process.argv.slice(2),args=new Map();
 for(let i=0;i<values.length;i+=2){
  if(!values[i].startsWith('--')||args.has(values[i])||values[i+1]===undefined)throw Error('invalid_arguments');
  args.set(values[i],values[i+1]);
 }
 const allowed={serve:['--directory'],status:['--directory'],watch:['--directory'],
  stop:['--directory','--command-id','--expected'],resume:['--directory','--command-id','--expected','--stopped-command-id'],
  receipt:['--directory','--command-id'],reconcile:['--directory','--command-id']};
 if(!allowed[command]||[...args.keys()].some(key=>!allowed[command].includes(key))||allowed[command].some(key=>!args.has(key)))throw Error('invalid_arguments');
 const directory=args.get('--directory'),emit=value=>process.stdout.write(JSON.stringify(value)+'\n');
 if(command==='serve'){
  let supervisor;
  const dispose=attachSupervisorSignalHandlers({current:()=>supervisor,onStopped:()=>process.exit(0),onRefused:code=>emit({status:'refused',code})});
  try{
   supervisor=await openInstalledService(directory);
   emit({ready:true,supervisorOnly:true,service:await supervisor.service.inspect()});
  }catch(error){dispose();throw error;}
  return;
 }
 const client=await connectInstalledService(directory);
 try{
  if(command==='status')emit(await client.service.inspect());
  else if(command==='watch'){
   client.subscribe(event=>emit(event));
   const close=()=>{client.close();process.exit(0);};
   process.once('SIGINT',close);process.once('SIGTERM',close);return;
  }else{
   const commandId=token(args.get('--command-id'));
   if(['receipt','reconcile'].includes(command))emit(await client.service[command](commandId));
   else{
    const raw=args.get('--expected');if(raw.length>4096)throw Error('request_limit');
    const expected=JSON.parse(raw);
    emit(await client.service[command]({commandId,expected,...(command==='resume'?{stoppedCommandId:token(args.get('--stopped-command-id'))}:{})}));
   }
  }
 }finally{if(command!=='watch')client.close();}
}
main().catch(error=>{
 const code=error instanceof Error&&/^[a-z_]{1,100}$/.test(error.message)?error.message:'service_command_unconfirmed';
 process.stderr.write(JSON.stringify({error:code,workReplayed:false,startupFailure:startupFailureFrom(error)})+'\n');process.exitCode=1;
});
