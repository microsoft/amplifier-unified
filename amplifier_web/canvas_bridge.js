/* Injected only into the opaque-origin HTML frame. This bridge cannot run app actions. */
(()=>{
 const id=__CANVAS_ID__,ids=new WeakMap(),elements=new Map();let sequence=0,failed=false,last='',timer;
 const send=(type,data)=>parent.postMessage({type,id,...data},'*');
 const report=(status,message)=>send('canvas-render',{status,message:String(message).slice(0,2000)});
 const snapshot=()=>{
  timer=null;elements.clear();
  const controls=[...document.querySelectorAll('button,input,select,textarea,[role="button"]')].filter(el=>el.getClientRects().length).slice(0,100).map(el=>{
   if(!ids.has(el))ids.set(el,'control-'+(++sequence)+'-'+Math.random().toString(36).slice(2,10));const key=ids.get(el);elements.set(key,el);
   const label=el.getAttribute('aria-label')||el.labels?.[0]?.textContent||el.textContent||el.getAttribute('placeholder')||el.id||el.tagName;
   return {id:key,tag:el.tagName.toLowerCase(),type:el.type||'',label:label.trim().slice(0,200),value:el.type==='password'||el.type==='file'?'[redacted]':String(['checkbox','radio'].includes(el.type)?el.checked:el.value||'').slice(0,4000),disabled:!!el.disabled};
  });
  const value={text:(document.body?.innerText||'').slice(0,16000),controls},serialized=JSON.stringify(value);
  if(serialized!==last){last=serialized;send('canvas-snapshot',{document:value})}
 };
 const schedule=()=>{if(!timer)timer=setTimeout(snapshot,250)};
 addEventListener('error',e=>{failed=true;report('error',e.message)});
 addEventListener('unhandledrejection',e=>{failed=true;report('error',e.reason)});
 addEventListener('DOMContentLoaded',()=>{
  new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true});
  addEventListener('input',schedule,true);addEventListener('change',schedule,true);addEventListener('click',schedule,true);
  snapshot();if(!failed)report('ready','HTML document loaded');
 });
 addEventListener('message',e=>{
  if(e.source!==parent||e.data?.type!=='canvas-interact'||e.data.id!==id)return;
  const el=elements.get(e.data.controlId);if(!el||!el.isConnected||el.disabled)return report('error','That control is no longer available. Read the current document.');
  try{
   if(e.data.event==='click')el.click();
   else if(e.data.event==='input'){
    if(el.type==='file'||el.type==='password')throw Error('This field requires direct user input.');
    if(['checkbox','radio'].includes(el.type))el.checked=e.data.value==='true';else el.value=String(e.data.value||'').slice(0,4000);
    el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
   }
   snapshot();
  }catch(error){report('error',error.message)}
 });
})();
