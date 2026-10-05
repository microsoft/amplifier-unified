/** Compose existing public ports; originals and retry state stay with their owners. */
export function composeOriginalAttachments(Host,getHost){
 const hostSupported=typeof Host.prototype.verifyOriginalForkAttachment==='function'&&typeof Host.prototype.readPublicOriginalRows==='function'&&typeof Host.prototype.reconcileOriginalForkAttachments==='function';
 const supported=resources=>hostSupported&&typeof resources.retainForkAttachments==='function'&&typeof resources.resolveOriginalAttachment==='function';
 const unavailable=message=>{throw Object.assign(Error(message),{data:{executed:false,replayed:false,reason:'original-attachment-unavailable'}})};
 return {
  resourceOptions:hostSupported?{verifyOriginalAttachment:request=>{const host=getHost();if(!host)unavailable('Host original attachment authority unavailable');return host.verifyOriginalForkAttachment(request);}}:{},
  hostOptions:resources=>supported(resources)?{publicOriginalResources:{retainOriginalAttachment:request=>resources.retainForkAttachments(request),resolveOriginalAttachment:(session,reference)=>resources.resolveOriginalAttachment(session,reference)}}:{},
  async resolvePromptAttachment(resources,context,attachment,options){
   if(!supported(resources)||typeof attachment.uri!=='string'||!attachment.uri.startsWith('amplifier-attachment://'))return resources.resolvePromptAttachment(context,attachment,options);
   const reference={owner:'resources',id:attachment.uri},grant=await resources.resolveOriginalAttachment(context.session,reference);
   if(grant===null)return resources.resolvePromptAttachment(context,attachment,options);
   if(grant?.version!==2||grant.replayed!==false||grant.targetHostSession!==context.session||grant.originalReference?.owner!==reference.owner||grant.originalReference.id!==reference.id||grant.status!=='completed'||typeof grant.accessUri!=='string'||!grant.accessUri)unavailable('Retained original attachment has no qualified target access');
   // Owner materialization validates URI scope and immutable body integrity.
   // Only the execution copy uses target access; the input card stays unchanged.
   return resources.resolvePromptAttachment(context,{...attachment,uri:grant.accessUri},options);
  },
 };
}
