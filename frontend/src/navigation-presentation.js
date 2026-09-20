export function activityFor(chat,state){
 if(chat.activity)return chat.activity;
 if(chat.approvals?.some(row=>row.status==null||row.status==='pending'))return {kind:'attention',label:'Approval requested'};
 if(['starting','working','running','stopping'].includes(chat.status))return {kind:'working',label:chat.status==='starting'?'Starting':chat.status==='stopping'?'Stopping':'Working'};
 if(chat.error||['error','failed'].includes(chat.status))return {kind:'attention',label:'Needs attention'};
 if(state?.attention?.sessions?.[chat.id])return {kind:'unread',label:'New response'};
 return {kind:'idle',label:'Idle'};
}
export function relativeActivity(at,now=Date.now()/1000){
 if(typeof at!=='number'||!Number.isFinite(at)||at<=0)return {short:'—',long:'Activity time unavailable'};
 const seconds=Math.max(0,now-at);
 if(seconds<60)return {short:'now',long:'Just now'};
 for(const [unit,size,next] of [['minute',60,3600],['hour',3600,86400],['day',86400,604800],['week',604800,Infinity]]){
  if(seconds<next){const n=Math.floor(seconds/size);return {short:n+unit[0],long:`${n} ${unit}${n===1?'':'s'} ago`};}
 }
}
export function parentPath(path=''){
 const trimmed=path.replace(/[\\/]+$/,'');
 const index=Math.max(trimmed.lastIndexOf('/'),trimmed.lastIndexOf('\\'));
 return index<0?'':trimmed.slice(0,index)||'/';
}
export function compactParent(path=''){
 const parent=parentPath(path),parts=parent.split(/[\\/]+/).filter(Boolean);
 return parts.length>2?'…/'+parts.slice(-2).join('/'):parent;
}

export function workspaceContext(row){
 const label=row.pathLabel||'',leaf=row.name||'';
 if(row.customName)return label||row.path;
 if(label.endsWith('/'+leaf))return label.slice(0,-leaf.length-1);
 return compactParent(row.path);
}
