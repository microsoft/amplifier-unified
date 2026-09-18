export async function request(path, options = {}) {
  const headers = { ...options.headers };
  let body = options.body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) { headers['Content-Type']='application/json'; body=JSON.stringify(body); }
  const res=await fetch(path,{...options,body,headers,credentials:'same-origin'});
  const content=await res.text(); let data;
  try { data=content?JSON.parse(content):{}; } catch { throw new Error(`The server returned an unexpected response (${res.status}).`); }
  if(!res.ok || data.accepted===false) throw new Error(typeof data.error==='string'?data.error:data.error?.message||data.message||`Request failed (${res.status})`);
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
export function visibleView(root, clientId) {
  if(!root)return {clientId};
  const controls=[...root.querySelectorAll('button,input,textarea,select,a')].filter(el=>el.getClientRects().length).map(el=>({id:el.id||null,action:el.dataset.action||el.closest('[data-action]')?.dataset.action||null,label:el.getAttribute('aria-label')||el.labels?.[0]?.textContent||el.textContent?.trim().slice(0,160),type:el.type||el.tagName.toLowerCase(),value:(el.type==='password'||el.dataset.private==='true')?'[redacted]':el.value,disabled:!!el.disabled,focused:document.activeElement===el}));
  return {clientId,visibleText:root.innerText.slice(0,60000),controls,selectedText:window.getSelection()?.toString().slice(0,10000)||'',viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY},url:location.pathname};
}
