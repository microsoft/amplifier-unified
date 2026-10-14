const string=max=>({type:'string',minLength:1,maxLength:max});const object=(properties,required=[])=>({type:'object',additionalProperties:false,properties,required});const hash={type:'string',pattern:'^[a-f0-9]{64}$'};
export const importActions={
 'history.import.begin':{description:'Stage one bounded JSON/JSONL source for review, without executing imported work.',schema:object({workingDirectory:string(8192),format:{enum:['json','jsonl']},bytes:{type:'integer',minimum:1,maximum:1048576},sha256:hash,title:string(1000),bundle:string(2048)},['workingDirectory','format','bytes','sha256'])},
 'history.import.chunk':{description:'Write the next exact at-most16KiB source chunk. Unknown commands must be inspected, never replayed.',schema:object({workflowId:string(200),offset:{type:'integer',minimum:0,maximum:1048576},contentBase64:string(21848)},['workflowId','offset','contentBase64'])},
 'history.import.preview':{description:'Validate the complete staged original and return a bounded review summary and exact preview hash.',schema:object({workflowId:string(200)},['workflowId'])},
 'history.import.commit':{description:'Explicitly confirm the reviewed native import and register its independently verified new session. No old work is replayed.',schema:object({workflowId:string(200),previewHash:hash},['workflowId','previewHash'])},
 'history.import.receipt':{description:'Read the exact original command receipt. Safe during maintenance; no mutation is resubmitted.',schema:object({commandId:string(200)},['commandId'])},
 'history.import.reconcile':{description:'Explicitly reconcile native receipt and derived host mapping only. Never repeats import or upload.',schema:object({commandId:string(200)},['commandId'])},
};
export const importLimits=Object.freeze({maxBytes:1048576,chunkBytes:16384,concurrentRequests:16,receiptBytes:32768});
