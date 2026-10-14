const string=maxLength=>({type:'string',minLength:1,maxLength});
const revision={type:'integer',minimum:0}, sid={sessionId:string(8192)}, identity={...sid,id:string(100)};
const entry=(description,properties,required=[])=>({description,inputSchema:{type:'object',properties,required,additionalProperties:false}});
export const actionSchemas={
  'worktree.inspect':entry('Inspect this conversation’s execution repository without starting an agent.',sid),
  'worktree.list':entry('Read a bounded page of this conversation’s checkout and handoff receipts.',{...sid,cursor:string(8192),limit:{type:'integer',minimum:1,maximum:50},collection:{enum:['worktrees','handoffs','commands']}}),
  'worktree.status':entry('Inspect one checkout and its preserved source manifest.',identity,['id']),
  'worktree.create':entry('Create a checkout at an exact inspected revision. Carrying uncommitted changes is explicit; the source remains unchanged.',{...sid,sourceRevision:string(100),mode:{enum:['clean','carry_dirty']},ref:string(500),branch:string(500)},['sourceRevision']),
  'worktree.attach':entry('Associate an existing Git worktree in the same repository. Does not grant cleanup ownership or start work.',{...sid,path:string(8192)},['path']),
  'worktree.remove':entry('Remove an owned clean checkout under a host directory guard. Preserve source, branch, history, and manifests.',{...identity,expectedRevision:revision},['id','expectedRevision']),
  'worktree.handoff':entry('Save and release the old native writer before changing only execution directory. Return a durable receipt; never replay input or unknown effects.',{...sid,id:{type:['string','null'],maxLength:100},expectedExecutionRevision:revision},['id','expectedExecutionRevision']),
  'worktree.reconcile':entry('Resolve an unknown handoff after inspecting actual native location evidence. Agent callers must cite a newer genuine user message. Never retry the old command.',{...identity,expectedRevision:revision,destination:{enum:['source','target']},evidence:string(4000),sourceMessageId:string(200)},['id','expectedRevision','destination','evidence']),
  'worktree.resolve':entry('Record a verified finding for an unknown Git operation without running Git effects. Completion requires matching durable library evidence; abandonment preserves files. Agent callers must cite a newer genuine user finding.',{...identity,expectedRevision:revision,resolution:{enum:['completed','abandoned']},evidence:string(4000),sourceMessageId:string(200)},['id','expectedRevision','resolution','evidence'])
};

export function validateArgs(operation,args) {
  const schema=actionSchemas[operation]?.inputSchema;
  if(!schema||!args||typeof args!=='object'||Array.isArray(args))throw Error('Invalid worktree action.');
  for(const key of schema.required)if(!(key in args))throw Error('Missing '+key);
  for(const [key,value] of Object.entries(args)) {
    const field=schema.properties[key];if(!field)throw Error('Unexpected worktree field '+key);
    if(field.enum&&!field.enum.includes(value))throw Error('Invalid '+key);
    if(value===null&&Array.isArray(field.type)&&field.type.includes('null'))continue;
    if(field.type==='integer'&&(!Number.isSafeInteger(value)||value<field.minimum||value>(field.maximum??Number.MAX_SAFE_INTEGER)))throw Error('Invalid '+key);
    if(field.type==='string'||Array.isArray(field.type)&&field.type.includes('string'))if(typeof value!=='string'||value.length<(field.minLength??0)||value.length>field.maxLength||value.includes('\0'))throw Error('Invalid '+key);
  }
}
