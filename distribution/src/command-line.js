import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {connectInstalledService,readInstalledServiceConfiguration} from './service.js';

export const help=`Amplifier Unified

  amplifier-unified serve --config /path/to/distribution.json
  amplifier-unified run "prompt" --server wss://host/ahp [--workspace /path]
  amplifier-unified continue "prompt" --server wss://host/ahp --resume ahp-session:/ID
  amplifier-unified tool NAME --args '{}' --server wss://host/ahp [--session ahp-session:/ID]
  amplifier-unified inspect --server wss://host/ahp --command REQUEST
  amplifier-unified doctor --data-dir /path/to/installation [--json]
  amplifier-unified service status --data-dir /path/to/installation [--wait 30] [--json]
  amplifier-unified service start|stop|restart --data-dir /path/to/installation [--wait 60] [--json]
  amplifier-unified service receipt --data-dir /path/to/installation --request REQUEST [--json]

Use AMPLIFIER_UNIFIED_INSTALLATION instead of --data-dir for an installed service.
Start resumes an existing, confirmed stopped installation. It never creates or adopts one.
An unconfirmed request is inspected by its request ID; it is never sent again automatically.
Run amplifier-unified run --help for authentication and task options.
Use Settings > Terminal for terminal setup.
`;

const failure=message=>Object.assign(Error(message),{code:'invalid_arguments'});
export function parseCommandLine(args,env=process.env){
 if(['run','continue','tool','inspect'].includes(args[0]))return {command:'headless',args:[...args]};
 if(!args.length||args.includes('--help')||args[0]==='help')return {command:'help'};
 if(args.length===1&&args[0]==='--version')return {command:'version'};
 const values=[...args];let command=values.shift();
 if(command==='--config'){values.unshift(command);command='serve';}
 let action;if(command==='service')action=values.shift();
 if(!['serve','doctor','service'].includes(command)||command==='service'&&!['status','start','stop','restart','receipt'].includes(action))throw failure('Unknown command. Run amplifier-unified --help.');
 const options={};
 while(values.length){
  const key=values.shift();
  if(Object.hasOwn(options,key))throw failure('An option was supplied more than once.');
  if(key==='--json'||key==='--verbose'){options[key]=true;continue;}
  if(!['--config','--data-dir','--directory','--wait','--request'].includes(key)||!values.length||values[0].startsWith('--'))throw failure('Unknown or incomplete option. Run amplifier-unified --help.');
  options[key]=values.shift();
 }
 if(command==='serve'){
  if(Object.keys(options).length!==1||!options['--config'])throw failure('Serve requires --config with an existing distribution configuration.');
  return {command,configuration:resolve(options['--config'])};
 }
 if(options['--config']||options['--directory']&&options['--data-dir']||command==='doctor'&&(options['--wait']||options['--request'])||action!=='receipt'&&options['--request'])throw failure('These options do not apply to this command.');
 const directory=options['--data-dir']??options['--directory']??env.AMPLIFIER_UNIFIED_INSTALLATION;
 if(!directory)throw failure('Choose the installation with --data-dir, or set AMPLIFIER_UNIFIED_INSTALLATION.');
 if(action==='receipt'&&(!options['--request']||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(options['--request'])))throw failure('Receipt requires the original --request ID.');
 const wait=options['--wait']===undefined?(action&&['start','stop','restart'].includes(action)?60:0):Number(options['--wait']);
 if(!Number.isInteger(wait)||wait<0||wait>120)throw failure('--wait must be a whole number from 0 to 120 seconds.');
 return {command,action,directory:resolve(directory),json:!!options['--json'],verbose:!!options['--verbose'],wait,request:options['--request']};
}

export async function inspectInstallation(directory,{connect=connectInstalledService}={}){
 const saved=await readInstalledServiceConfiguration(directory),client=await connect(directory);
 try{
  const service=await client.service.inspect();
  const checks=[{id:'configuration',ok:true,message:'The saved installation and service configuration match.'},
   {id:'service',ok:service.state==='running'&&service.ownership==='verified',message:service.state==='running'&&service.ownership==='verified'?'The service is running with verified ownership.':service.state==='stopped'?'The service is stopped.':'The service state needs inspection.'}];
  return {ok:checks.every(c=>c.ok),installation:saved.kind,state:service.state,checks,service,
   limits:['Provider sign-in and physical microphone or notification access are checked in Settings.'],workReplayed:false};
 }finally{client.close();}
}

async function receiptUntil(client,id,wait){
 const end=Date.now()+wait*1000;
 for(;;){
  const receipt=await client.service.receipt(id);
  if(!receipt||receipt.status!=='running'||Date.now()>=end)return receipt;
  await delay(Math.min(200,Math.max(0,end-Date.now())));
 }
}
export async function manageService(options,{connect=connectInstalledService,onRequest=()=>{}}={}){
 const client=await connect(options.directory);
 try{
  if(options.action==='receipt')return {receipt:await client.service.receipt(options.request),workReplayed:false};
  let state=await client.service.inspect();
  if(options.action==='status'){
   const end=Date.now()+options.wait*1000;
   while(state.state!=='running'&&Date.now()<end){await delay(Math.min(200,end-Date.now()));state=await client.service.inspect();}
   return {service:state,workReplayed:false};
  }
  if(!['running','stopped'].includes(state.state)||!state.identity)throw Error('service_state_requires_inspection');
  if(options.action==='start'&&state.state==='running'||options.action==='stop'&&state.state==='stopped')return {service:state,changed:false,workReplayed:false};
  const receipts=[];
  async function submit(operation,args){
   const commandId='cli-'+randomUUID();
   // Announce the original identity before dispatch, even when the reply is lost.
   onRequest({request:commandId,operation});
   try{
    await client.service[operation]({commandId,...args});
    const receipt=await receiptUntil(client,commandId,options.wait);receipts.push(receipt);
    return receipt;
   }catch{throw Object.assign(Error('service_request_unconfirmed'),{request:commandId});}
  }
  if(state.state==='running'){
   const stopped=await submit('stop',{expected:state.identity});
   if(options.action==='stop'||stopped?.status!=='stopped')return {receipts,workReplayed:false};
   // Restart can proceed only from this exact acknowledged stop, never a fresh observation.
   state={state:'stopped',identity:stopped.expected,stoppedReceiptId:stopped.commandId};
  }
  if(!state.stoppedReceiptId)throw Error('service_stop_receipt_required');
  await submit('resume',{expected:state.identity,stoppedCommandId:state.stoppedReceiptId});
  return {receipts,workReplayed:false};
 }finally{client.close();}
}

export async function runCommandLine(options,{out=value=>process.stdout.write(value),error=value=>process.stderr.write(value)}={}){
 if(options.command==='headless'){process.argv=[...process.argv.slice(0,2),...options.args];await import('@amplifier/ahp-client-kit/cli');return process.exitCode??0;}
 if(options.command==='help'){out(help);return 0;}
 if(options.command==='version'){const p=JSON.parse(await readFile(new URL('../package.json',import.meta.url)));out('amplifier-unified '+p.version+'\n');return 0;}
 try{
  const result=options.command==='doctor'?await inspectInstallation(options.directory):await manageService(options,{onRequest:r=>error(options.json?JSON.stringify(r)+'\n':'Request '+r.request+' ('+r.operation+')\n')});
  if(options.json)out(JSON.stringify(result)+'\n');
  else if(options.command==='doctor'){
   for(const check of result.checks)out((check.ok?'OK: ':'Attention: ')+check.message+'\n');
   for(const limit of result.limits)out(limit+'\n');
  }else{
   if(result.service)out('Unified is '+result.service.state+'.\n');
   for(const r of result.receipts??(result.receipt?[result.receipt]:[]))out('Request '+r.commandId+': '+r.status+'.\n');
   if(options.action==='receipt'&&!result.receipt)out('No saved receipt was found. This does not authorize sending the request again.\n');
  }
  if(options.command==='doctor')return result.ok?0:1;
  if(result.service)return ['running','stopped'].includes(result.service.state)?0:1;
  const receipts=result.receipts??[result.receipt];return receipts.every(r=>r&&['stopped','ready'].includes(r.status)&&(!r.admissionSettlement||r.admissionSettlement.state==='settled'))?0:1;
 }catch(e){
  const code=/^[a-z_]{1,100}$/.test(e.message)?e.message:'installation_inspection_failed';
  const result={ok:false,code,...(e.request?{request:e.request}:{}),workReplayed:false,
   guidance:e.request?'Inspect the original request with service receipt --request '+e.request+'.':'Check the selected installation and its saved service configuration. Existing data was preserved.'};
  error(options.json?JSON.stringify(result)+'\n':'Unified needs attention: '+result.guidance+'\n'+(options.verbose?'Details: '+code+'\n':''));return 1;
 }
}
