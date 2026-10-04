// Public ACP fixture; a controlled local barrier, no model or account.
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {writeFile,access,appendFile} from 'node:fs/promises';
const send=value=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...value})+'\n');
createInterface({input:process.stdin}).on('line',line=>{void(async()=>{
 const {id,method,params}=JSON.parse(line);if(id===undefined)return;
 if(method==='initialize')return send({id,result:{protocolVersion:1,agentCapabilities:{sessionCapabilities:{resume:{},close:{}}},authMethods:[]}});
 if(method==='session/new')return send({id,result:{sessionId:randomUUID(),configOptions:[]}});
 if(['session/resume','session/close'].includes(method))return send({id,result:{}});
 if(method==='session/prompt'){
  await appendFile(process.env.AUDIT,'prompt\n');await writeFile(process.env.STARTED,'started');
  for(;;){try{await access(process.env.RELEASE);break;}catch{await new Promise(r=>setTimeout(r,10));}}
  send({method:'session/update',params:{sessionId:params.sessionId,update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'Completed after device revocation'}}}});return send({id,result:{stopReason:'end_turn'}});
 }
 send({id,error:{code:-32601,message:'Fixture method unavailable'}});
})();});
