// Synthetic retained work only. No operation here is executable or live state.
export function workHierarchyData(){
 const turnId='failed-turn';
 const worker={id:'reviewer',ownerId:'reviewer',turnId,kind:'worker',sessionId:'child-fixture',rootSessionId:'main-fixture',label:'Review child',phase:'completed',exchange:{id:'delegation-fixture',startedAt:4,endedAt:8,delegateRequest:'Review the synthetic result.',delegateResponse:'Retained reply, not new work.'}};
 return {sessionId:'main-fixture',turns:[
  {id:turnId,phase:'error',startedAt:1,endedAt:10,detailRevision:'fixture-v1',nodeCounts:{tools:2,models:1},aggregateUsage:{calls:1,inputTokens:12,outputTokens:3}},
  {id:'worked-turn',phase:'completed',startedAt:12,endedAt:14},
 ],nodes:[
  {id:'root-tool',turnId,kind:'tool',label:'app_control',phase:'error',startedAt:2,endedAt:3,input:JSON.stringify({operation:'inspect',args:{id:'synthetic-record-with-a-long-wrapped-name'}}),output:JSON.stringify({accepted:false,error:'Retained synthetic failure; never replay.'}),savedObservations:[{id:'unlinked-record',kind:'tool',label:'saved_record',phase:'recorded',input:'Unlinked retained text',association:'unverified'}]},
  {id:'root-model',turnId,kind:'llm',label:'Model request',provider:'fixture-provider',model:'fixture-model-with-a-long-name',phase:'completed',startedAt:3,endedAt:4,requestInfo:{reasoning_effort:'low',max_tokens:1024}},
  worker,
  {id:'child-tool',turnId,kind:'tool',label:'read_file',parentId:'reviewer',sessionId:'child-fixture',rootSessionId:'main-fixture',phase:'completed',startedAt:5,endedAt:6,input:'{"file_path":"synthetic-only.txt"}',output:'{"content":"Retained child result."}'},
  {id:'root-context',turnId,kind:'context',label:'Context processing',summary:'Retained context processing observation, not a new compaction.',phase:'completed',startedAt:7,endedAt:8},
  {id:'sibling-tool',turnId:'worked-turn',kind:'tool',label:'read_file',phase:'completed',startedAt:12,endedAt:14,input:'{"file_path":"sibling.txt"}',output:'{"content":"Sibling retained result."}'},
 ]};
}
