// Both app-control agents and the workspace UI call the same Host reader.
export const planReadAction={
 description:'Read recorded plan progress for up to 50 explicit conversation IDs, optionally including steps. Does not select chats or start work.',
 schema:{type:'object',properties:{ids:{type:'array',maxItems:50,uniqueItems:true,items:{type:'string',pattern:'^ahp-session:/[^?#\\s]+$'}},includeItems:{type:'boolean'}},required:['ids'],additionalProperties:false}
};
export async function readPlans(host,args){return {accepted:true,result:await host.sessionPlans(args)};}
