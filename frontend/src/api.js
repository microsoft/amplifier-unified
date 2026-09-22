// A page owns one presentation identity. Reload/duplicate restores a copy of
// the previous view, never the same writable client as another live tab.
export const clientId=crypto.randomUUID();
let attachment;
export function clientUrl(path){
  if(!path.startsWith('/api/'))return path;
  const url=new URL(path,location.origin);url.searchParams.set('clientId',clientId);
  return url.pathname+url.search;
}
export function attachClient(signal){
  if(!attachment){
    let resumeClientId;try{resumeClientId=sessionStorage.getItem('amplifier.clientId')||undefined}catch{}
    attachment=request('/api/clients/attach',{method:'POST',signal,body:{clientId,resumeClientId,kind:'web',protocolVersion:1}}).then(result=>{
      try{sessionStorage.setItem('amplifier.clientId',clientId)}catch{}
      return result;
    }).catch(error=>{attachment=null;throw error});
  }
  return attachment;
}

// Host response time anchors live elapsed displays even when a device clock differs.
let hostClock;
export function hostNow(){return hostClock?hostClock.seconds+(performance.now()-hostClock.received)/1000:Date.now()/1000}
export async function request(path, options = {}) {
  const headers = { ...(path.startsWith('/api/')?{'X-Amplifier-Client':clientId}:{}), ...options.headers };
  let body = options.body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) { headers['Content-Type']='application/json'; body=JSON.stringify(body); }
  const res=await fetch(path,{...options,body,headers,credentials:'same-origin'});
  const dated=Date.parse(res.headers?.get?.('date'));
  // Second-precision or delayed responses cannot wind an active timer back.
  if(Number.isFinite(dated)&&(!hostClock||dated/1000>hostNow()))hostClock={seconds:dated/1000,received:performance.now()};
  const content=await res.text(); let data;
  try { data=content?JSON.parse(content):{}; } catch { throw new Error(`The server returned an unexpected response (${res.status}).`); }
  // A repeated command can return its original rejection inside an HTTP 200 receipt.
  const rejectionStatus=res.ok&&data.accepted===false?(Number.isInteger(data.status)&&data.status>=400&&data.status<600?data.status:400):res.status;
  if(!res.ok || data.accepted===false) throw Object.assign(new Error(typeof data.error==='string'?data.error:data.error?.message||data.message||`Request failed (${rejectionStatus})`),{code:data.code,state:data.state,status:rejectionStatus});
  return data;
}
export function download(filename, content, type='application/json') {
  const blob=new Blob([typeof content==='string'?content:JSON.stringify(content,null,2)],{type});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function applyIconTooltips(root) {
  if(!root)return;
  for(const control of root.querySelectorAll('button[aria-label],a[aria-label],[role="button"][aria-label]')){
    if(!control.classList?.contains('a-icon')&&!control.querySelector?.('svg'))continue;
    const label=control.getAttribute('aria-label');
    if(label&&(!control.title||control.dataset.iconTitle==='auto')){
      control.title=label;
      control.dataset.iconTitle='auto';
    }
  }
}
// Repeated browser observations describe client-owned UI. Conversation text and
// elapsed progress already have an authoritative, session-scoped server view.
// Keep full rendered text for explicit window.amplifier.getState() inspection.
function interfaceText(root){
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,{acceptNode(node){
    if(node.nodeType===Node.ELEMENT_NODE){const style=getComputedStyle(node);return node.dataset.viewSource||style.display==='none'||style.visibility==='hidden'?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_SKIP;}
    return node.textContent.trim()?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_SKIP;
  }});
  let node,text='';
  while((node=walker.nextNode())&&text.length<60000)text+=node.textContent.trim()+'\n';
  return text.slice(0,60000);
}
export function visibleView(root, clientId, {interfaceOnly=false}={}) {
  if(!root)return {clientId};
  const visible=el=>!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden';
  const controls=[...root.querySelectorAll('button,input,textarea,select,a,[role=separator]')].filter(visible).map(el=>({id:el.id||null,action:el.dataset.action||el.closest('[data-action]')?.dataset.action||null,...(el.closest('[data-shell-component]')?{component:{instanceId:el.closest('[data-shell-component]').dataset.shellComponent,slot:el.closest('[data-shell-component]').dataset.shellSlot,command:el.dataset.shellCommand||null}}:{}),label:el.dataset.viewLabel||el.getAttribute('aria-label')||el.labels?.[0]?.textContent||el.textContent?.trim().slice(0,160),type:el.getAttribute('role')||el.type||el.tagName.toLowerCase(),...(el.getAttribute('role')==='separator'?{minimum:Number(el.getAttribute('aria-valuemin')),maximum:Number(el.getAttribute('aria-valuemax')),current:Number(el.getAttribute('aria-valuenow'))}:{}),value:(el.type==='password'||el.dataset.private==='true')?'[redacted]':el.value,disabled:!!el.disabled||!!el.closest('[inert]'),...(el.hasAttribute('aria-expanded')?{expanded:el.getAttribute('aria-expanded')==='true'}:{}),focused:document.activeElement===el}));
  const panes=Object.fromEntries(['navigation','conversation','canvas'].flatMap(name=>{const el=root.querySelector(`[data-part="${name}"]`);if(!el)return [];const box=el.getBoundingClientRect();return [[name,{x:Math.round(box.x),y:Math.round(box.y),width:Math.round(box.width),height:Math.round(box.height),visible:visible(el),interactive:visible(el)&&!el.closest('[inert]')}]]}));
  return {clientId,panes,webApp:{standalone:!!(window.matchMedia?.('(display-mode: standalone)').matches||navigator.standalone),secureContext:window.isSecureContext,serviceWorkerControlled:!!navigator.serviceWorker?.controller,online:navigator.onLine},visibleText:interfaceOnly?interfaceText(root):root.innerText.slice(0,60000),...(interfaceOnly?{visibleTextScope:'interface',conversationSource:'Selected session messages, execution and activity in app state'}:{}),controls,selectedText:window.getSelection()?.toString().slice(0,10000)||'',viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY},url:location.pathname};
}
