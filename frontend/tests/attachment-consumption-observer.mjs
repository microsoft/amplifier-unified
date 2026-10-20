// Browser-only test sensor. Never edits the product DOM, outbox, or draft state.
// Inject with addInitScript before navigation; detector counterchecks are separate.
export function installAttachmentObserver() {
 const selector='.a-composer';
 const rules=new Map(),known=new WeakSet(),cachedIds=new WeakMap();
 const events=[],violations=[];
 let capped=false,frame=0,raf,observer,last='',stopped=false;
 const idOf=value=>/\/api\/attachments\/([a-f0-9]{32})(?:[?#]|$)/.exec(value||'')?.[1];
 const push=row=>{if(events.length<6000)events.push({at:performance.now(),frame,...row});else capped=true;};
 function ids(node,retainDetached=false){
  if(node?.nodeType!==1)return [];
  // Cached IDs recover removed subtree evidence only. Never merge old IDs into
  // the current form snapshot: that would manufacture reappearance.
  const found=new Set(retainDetached?cachedIds.get(node)||[]:[]);
  for(const el of [node,...node.querySelectorAll('[href],[src]')]){
   const id=idOf(el.getAttribute('href'))||idOf(el.getAttribute('src'));
   if(id)found.add(id);
  }
  return [...found];
 }
 function view(){
  const state=window.amplifier?.getState?.();
  const owner=state?.selectedSessionId??null;
  const session=state?.sessions?.find(row=>row.id===owner);
  const form=document.querySelector(selector);
  let outbox=[];try{outbox=JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]')}catch{}
  return {owner,client:state?.client?.id,host:state?.client?.hostInstanceId,
   revision:state?.revision,status:session?.status,
   sourceIds:(session?.draftAttachments||state?.draftAttachments||[]).map(row=>row.id),
   domIds:form?ids(form):[],
   outbox:outbox.map(row=>({id:row.commandId,owner:row.sessionId,status:row.status,ids:row.attachmentIds})),
   accepted:(session?.messages||[]).filter(row=>row.delivery?.status==='accepted').map(row=>row.inputId),
   editable:form?.querySelector('[role="textbox"]')?.getAttribute('contenteditable'),
   sendDisabled:form?.querySelector('[data-action="conversation.send"]')?.disabled,
   textLength:state?.view?.draft?.length||0};
 }
 function forbidden(list,kind){
  for(const id of new Set(list))if(rules.get(id)?.armed){
   const row={id,kind,owner:rules.get(id).owner,at:performance.now(),frame};
   if(violations.length<100)violations.push(row);else capped=true;
   push({kind:'forbidden-reappearance',...row});
  }
 }
 function sample(kind){
  const state=view(),form=document.querySelector(selector);
  if(form)for(const node of [form,...form.querySelectorAll('*')]){
   known.add(node);cachedIds.set(node,ids(node));
  }
  for(const [id,rule] of rules)if(!rule.armed&&state.owner===rule.owner&&!state.domIds.includes(id)){
   rule.armed=true;push({kind:'optimistic-clear-observed',id,owner:rule.owner});
  }
  forbidden(state.domIds,kind);
  const signature=JSON.stringify(state);
  if(signature!==last||kind==='checkpoint'){push({kind,state});last=signature;}
 }
 const mutations=records=>{
  for(const record of records){
   const local=known.has(record.target)||record.target?.closest?.(selector);
   if(!local)continue;
   if(record.type==='childList'){
    const added=[...record.addedNodes].flatMap(node=>ids(node)),removed=[...record.removedNodes].flatMap(node=>ids(node,true));
    if(added.length||removed.length){push({kind:'composer-mutation',added,removed});forbidden(added,'added-node');}
   }else if(['src','href'].includes(record.attributeName)){
    const after=ids(record.target),old=idOf(record.oldValue);
    push({kind:'composer-attribute',after,old});forbidden(after,'attribute');
   }
  }
  sample('mutation-boundary');
 };
 const tick=()=>{if(stopped)return;frame++;if(frame>18000){capped=true;return;}sample('animation-frame');raf=requestAnimationFrame(tick);};
 const start=()=>{if(observer)return;observer=new MutationObserver(mutations);observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeOldValue:true,attributeFilter:['src','href','disabled','contenteditable']});raf=requestAnimationFrame(tick);};
 window.attachmentTrace={
  watch(owner,list,alreadyConsumed=false){sample('checkpoint');for(const id of list){if(rules.has(id))throw Error('Duplicate watch identity');rules.set(id,{owner,armed:alreadyConsumed});}push({kind:'watch',owner,ids:list,alreadyConsumed});},
  read(){sample('checkpoint');return {events:events.slice(),violations:violations.slice(),rules:[...rules].map(([id,rule])=>({id,...rule})),capped,frame};},
  stop(){if(observer)mutations(observer.takeRecords());sample('checkpoint');stopped=true;observer?.disconnect();cancelAnimationFrame(raf);return this.read();},
 };
 if(document.documentElement)start();else document.addEventListener('DOMContentLoaded',start,{once:true});
}
