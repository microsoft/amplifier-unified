// Independent ACP v1 peer for distribution wiring; no product imports or paid calls.
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {appendFileSync} from 'node:fs';
const send=value=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...value})+'\n');
createInterface({input:process.stdin}).on('line',line=>{
 const {id,method,params}=JSON.parse(line);if(id===undefined)return;
 if(method==='initialize')return send({id,result:{protocolVersion:1,agentCapabilities:{promptCapabilities:{embeddedContext:true,image:true},sessionCapabilities:{resume:{},close:{}}},authMethods:[]}});
 if(method==='session/new')return send({id,result:{sessionId:randomUUID(),configOptions:[]}});
 if(['session/resume','session/close'].includes(method))return send({id,result:{}});
 if(method==='session/prompt'){if(process.env.OWNED_PROMPT_LOG)appendFileSync(process.env.OWNED_PROMPT_LOG,JSON.stringify(params.prompt)+'\n');send({method:'session/update',params:{sessionId:params.sessionId,update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'Independent fixture'}}}});return send({id,result:{stopReason:'end_turn'}});}
 send({id,error:{code:-32601,message:'Fixture method unavailable'}});
});
