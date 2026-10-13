// Presentation consumes host identities, never content hashes or renderer grants.
const inlineKinds=new Set(['image','html','babylon','mermaid','dot']);
const nativeKinds=new Set(['markdown','text','code','json','jsonl','image','html','babylon','mermaid','dot','pdf']);
const mediaKinds=new Set(['gif','mp4']);
const modes=new Set(['none','compact','inline']);

export function publicationMode(item){
 return modes.has(item.publication?.chat)?item.publication.chat:inlineKinds.has(item.row.kind)?'inline':'compact';
}

export function artifactPublications(rows,messageId){
 return rows.filter(row=>row.kind!=='settings').flatMap(row=>{
  const links=row.publications??[{messageId:row.messageId,version:row.app?.revision||row.revision||1}];
  return links.filter(link=>link.messageId===messageId&&Number.isSafeInteger(link.version)&&link.version>0)
   .map(publication=>({row,version:publication.version,publication}));
 });
}

export function presentationCapabilities(item){
 const kind=item.row.kind,caps=item.row.presentationCapabilities;
 const legacy=nativeKinds.has(kind);
 // Advanced views need their qualified native lifecycle, not just a renderer
 // package or an active capability grant. That consumer is a separate unit.
 const mountable=legacy||mediaKinds.has(kind);
 const inline=mountable&&(caps?caps.inline===true:legacy);
 return {compact:caps?caps.compact===true:true,inline,canvas:caps?caps.canvas===true:true,
  reason:inline?'':caps?.reason||(mountable?'Inline preview is unavailable on this host.':'Inline lifecycle qualification is pending for this artifact. Open it in Canvas.')};
}

export function artifactOverrideKey(state,item,messageId){
 return JSON.stringify([state.client?.hostInstanceId??null,item.row.sessionId,messageId,item.row.id,item.version]);
}

export function canvasOpenCommand(item,messageId,actions=[]){
 const names=new Set(actions.map(row=>row.name||row.action));
 const {row,version}=item;
 if(names.has('canvas.present'))return {action:'canvas.present',args:{
  sessionId:row.sessionId,id:row.id,version,messageId,
  // A private expansion is not a publication edit.
  presentation:{chat:publicationMode(item),canvas:'open'}
 }};
 // Explicit compatibility for hosts predating canvas.present. Never retry a
 // failed mutation under this different action.
 return {action:'canvas.select',args:{id:row.id,version}};
}

export function canvasOpenNotice(receipt){
 if(receipt?.accepted===false)return receipt.error||'The Canvas request was not accepted.';
 const outcome=receipt?.result?.canvas;
 if(outcome?.status==='opened')return '';
 return outcome?.reason||outcome?.status&&`Canvas was not opened (${outcome.status}).`||'Canvas opening could not be confirmed.';
}

// Proposed explicit compact-summary binding: publication.outputId identifies
// the retained registry item for this exact publication version. A row-level
// outputId or equal body/hash is insufficient when versions differ.
export function publicationOutputIds(rows,messageId){
 return new Set(artifactPublications(rows,messageId).map(item=>item.publication.outputId)
  .filter(id=>typeof id==='string'&&id.length>0));
}