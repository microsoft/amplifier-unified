/** Resolve settings ownership through the public workspace catalog and Host. */
export async function configurationContext(target,args,context,{catalog,host,defaultWorkspace}){
 const refuse=()=>{throw Object.assign(Error('Choose one authorized configuration owner.'),{data:{executed:false,replayed:false,reason:'configuration-target-authority'}})};
 if(context.session||!target||typeof target!=='object'||Array.isArray(target)||['workspace','location','nativeProject','sessionId'].some(key=>Object.hasOwn(args,key)))refuse();
 if(target.kind==='global'&&Object.keys(target).length===1){
  if(args.scope!==undefined&&args.scope!=='global')refuse();
  return {...context,workingDirectory:defaultWorkspace};
 }
 if(target.kind!=='workspace'||Object.keys(target).sort().join(',')!=='kind,workspaceId'||typeof target.workspaceId!=='string'||!target.workspaceId||target.workspaceId.length>200||/[\x00-\x1f]/.test(target.workspaceId)||args.scope!==undefined&&!['local','project'].includes(args.scope)||!catalog)refuse();
 const workspace=await catalog.getWorkspace({id:target.workspaceId});
 if(!workspace||workspace.id!==target.workspaceId||typeof workspace.path!=='string'||workspace.available===false)refuse();
 return {...context,workingDirectory:await host.authorizeWorkspace(workspace.path)};
}
