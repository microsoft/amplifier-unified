/** Composition validates names; feature owners retain their own storage and behavior. */
export function composeCapabilities(owners,{account}={}){
 const topics={},actions={},actionSchemas={},byTopic=new Map(),byAction=new Map();
 for(const owner of owners){
  if(owner.manifest?.version!==1)throw Error('Unsupported capability manifest');
  for(const [name,topic]of Object.entries(owner.manifest.topics??{})){if(byTopic.has(name))throw Error('Duplicate capability topic: '+name);topics[name]=topic;byTopic.set(name,owner);}
  for(const [name,action]of Object.entries(owner.manifest.actions??{})){if(actions[name])throw Error('Duplicate capability action: '+name);const key=JSON.stringify([action.topic,action.operation]);if(byAction.has(key))throw Error('Duplicate capability operation: '+key);actions[name]=action;byAction.set(key,owner);if(owner.actionSchemas?.[name])actionSchemas[name]=owner.actionSchemas[name];}
 }
 return {
  manifest:{version:1,topics,actions},
  actionSchemas,
  async getActionSchemas(){
   const result={};
   for(const owner of owners){const definitions=typeof owner.actionSchemas==='function'?await owner.actionSchemas():owner.actionSchemas??{};
    for(const [name,value]of Object.entries(definitions)){if(!owner.manifest.actions[name])throw Error('Schema has no advertised capability action: '+name);result[name]={description:value.description,schema:value.schema??value.inputSchema};}
   }
   if(Buffer.byteLength(JSON.stringify(result))>512*1024)throw Error('Action catalog exceeds capacity');return result;
  },
  async read(request,context={}){const owner=byTopic.get(request.topic);if(!owner)throw Error('Capability topic unavailable');return owner.read({...request,account},{...context,clientId:request.clientId,account});},
  async action(request,context){const owner=byAction.get(JSON.stringify([request.topic,request.operation]));if(!owner)throw Error('Capability action unavailable');return owner.action(request,{...context,account});},
  resources:owners.flatMap(owner=>owner.resourceProviders??(owner.resourceProvider?[owner.resourceProvider]:[])),
  async close(){await Promise.allSettled(owners.map(owner=>owner.close?.()));}
 };
}
