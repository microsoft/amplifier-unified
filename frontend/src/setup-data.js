export function providerConfig(text){
 const value=JSON.parse(text);
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Provider configuration must be a JSON object.');
 function check(node){
  if(!node||typeof node!=='object')return;
  for(const[key,item]of Object.entries(node)){
   if(/api.?key|password|secret|(?:access|refresh|github|auth).?token/i.test(key)&&typeof item==='string'&&!/^\$\{[A-Za-z_][A-Za-z0-9_]*\}$/.test(item)&&!/^\[redacted\]$/i.test(item))throw new Error('Enter credentials in the private API key field, or use an environment reference in configuration.');
   check(item);
  }
 }
 check(value);
 return value;
}
export function modelOptions(value){
 const models=Array.isArray(value)?value:value?.models||[];
 return models.map(model=>typeof model==='string'?{id:model,name:model}:{id:model.id||model.name||model.model,name:model.display_name||model.displayName||model.name||model.id||model.model}).filter(model=>model.id);
}
export function updateRole(matrix,role,patch){
 return {...matrix,roles:{...matrix?.roles,[role]:{...matrix?.roles?.[role],...patch}}};
}
export function updateCandidate(matrix,role,index,patch){
 const candidates=[...(matrix?.roles?.[role]?.candidates||[])];
 candidates[index]={...candidates[index],...patch};
 return updateRole(matrix,role,{candidates});
}

export function blankRouting(name='my-routing'){
 return {name,roles:{general:{description:'General conversation and work',candidates:[{provider:'',model:''}]},fast:{description:'Quick and lightweight work',candidates:[{provider:'',model:''}]}}};
}
export function safeLoginUrl(value){
 try{const url=new URL(value);return url.protocol==='https:'?url.href:null}catch{return null}
}

export function providerFields(metadata,config={}){
 const fields=metadata?.configSchema?.fields||metadata?.info?.config_fields||[];
 return fields.filter(field=>field.id&&field.field_type!=='secret'&&!/api.?key|password|secret|(?:access|refresh|github|auth).?token/i.test(field.id)&&!['model','default_model'].includes(field.id)).filter(field=>{
  if(field.requires_model&&!config.default_model&&!config.model)return false;
  return Object.entries(field.show_when||{}).every(([key,expected])=>{
   const actual=String(config[key]??'').toLowerCase(),value=String(expected).toLowerCase();
   if(value.startsWith('matches:')){try{return new RegExp(String(expected).slice(8),'i').test(actual)}catch{return false}}
   for(const[prefix,test] of [['not_contains:',v=>!actual.includes(v)],['contains:',v=>actual.includes(v)],['not_startswith:',v=>!actual.startsWith(v)],['startswith:',v=>actual.startsWith(v)]])if(value.startsWith(prefix))return test(value.slice(prefix.length));
   return actual===value;
  });
 });
}
