// DOM metadata is readable from an isolated browser inspection context. Keep
// only fixed labels and numeric evidence; never resource values or query text.
const reasons=new Set(['bootstrap','demand','visibility','reconnect','notify','resources','command-view','manual','composition','stale','scope-mismatch','error']);
const resources=new Set(['context','customSnapshot','chatNavigation','homeNavigation','pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarWorkspace','sidebarRecent','sidebarPinned','workspaceOverview']);
export function createShellDiagnostics(target=globalThis.document?.documentElement){
 const events=[],counts={};
 const record=(reason,fields={})=>{
  if(!reasons.has(reason))return;
  counts[reason]=(counts[reason]||0)+1;
  const entry={reason,at:Math.round(globalThis.performance?.now?.()||0)};
  for(const key of ['demandId','elapsedMs','serverMs','status','updates','bytes']){
   const value=fields[key];if(Number.isFinite(value)&&value>=0)entry[key]=Math.min(Math.round(value),Number.MAX_SAFE_INTEGER);
  }
  if(typeof fields.requestId==='string'&&/^[a-f0-9]{32}$/.test(fields.requestId))entry.requestId=fields.requestId;
  if(Array.isArray(fields.resources))entry.resources=[...new Set(fields.resources.filter(value=>resources.has(value)))].slice(0,13);
  events.push(entry);if(events.length>24)events.shift();
  let value=JSON.stringify({version:1,counts,events});
  while(value.length>8192&&events.length){events.shift();value=JSON.stringify({version:1,counts,events})}
  target?.setAttribute?.('data-amplifier-shell-diagnostics',value);
 };
 return {record};
}
