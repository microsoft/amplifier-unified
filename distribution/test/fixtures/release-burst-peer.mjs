import readline from 'node:readline';
const send=m=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...m})+'\n');
for await (const line of readline.createInterface({input:process.stdin})){
 const m=JSON.parse(line),p=m.params??{};let result={};
 if(m.method==='initialize')result={protocolVersion:1,agentInfo:{name:'audit-scripted',version:'1'},agentCapabilities:{loadSession:true},authMethods:[]};
 else if(m.method==='session/new')result={sessionId:'native-'+m.id};
 else if(m.method==='session/prompt'){
  const text=p.prompt.filter(x=>x.type==='text').map(x=>x.text).join(' '),n=text==='burst'?1000:20;
  for(let i=0;i<n;i++)send({method:'session/update',params:{sessionId:p.sessionId,update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'x'.repeat(64)}}}});
  if(text==='huge')send({method:'session/update',params:{sessionId:p.sessionId,update:{sessionUpdate:'tool_call',toolCallId:'large',title:'Large synthetic tool',kind:'read',status:'completed',content:[{type:'content',content:{type:'text',text:'z'.repeat(1024*1024)}}]}}});
  result={stopReason:'end_turn'};
 }
 if(m.id!==undefined)send({id:m.id,result});
}
