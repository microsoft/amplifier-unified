const pair={type:'string',pattern:'^[a-f0-9]{64}$'};
export const attentionActions={
 'attention.read':{description:'Mark only the exact displayed settings notices reviewed across clients. This does not resolve, retry, or dismiss the underlying operation.',schema:{type:'object',additionalProperties:false,required:['ids','fingerprints'],properties:{ids:{type:'array',maxItems:50,uniqueItems:true,items:{type:'string',pattern:'^settings:[a-f0-9]{64}$'}},fingerprints:{type:'object',maxProperties:50,additionalProperties:pair}}}},
};
export function reviewRequest(args){
 if(!args||Array.isArray(args)||Object.keys(args).some(k=>!['ids','fingerprints'].includes(k))||!Array.isArray(args.ids)||args.ids.length>50||new Set(args.ids).size!==args.ids.length||!args.fingerprints||typeof args.fingerprints!=='object'||Array.isArray(args.fingerprints)||Object.keys(args.fingerprints).length!==args.ids.length)throw Error('Choose at most 50 exact settings notices.');
 return args.ids.map(id=>{if(typeof id!=='string'||!/^settings:[a-f0-9]{64}$/.test(id)||typeof args.fingerprints[id]!=='string'||!/^[a-f0-9]{64}$/.test(args.fingerprints[id]))throw Error('Exact settings notice required.');return {id,fingerprint:args.fingerprints[id]};});
}
export function attentionFacts(value){
 if(!value||!Array.isArray(value.items)||value.items.length>200||Buffer.byteLength(JSON.stringify(value))>96000)throw Error('Host attention exceeds its bounded representation');
 const ids=new Set();
 for(const row of value.items){reviewRequest({ids:[row.id],fingerprints:{[row.id]:row.fingerprint}});if(ids.has(row.id)||row.sessionId||typeof row.acknowledge!=='boolean')throw Error('Invalid host attention fact');ids.add(row.id);}
 return value;
}
