import React from 'react';
export function AttentionBadge({state,section,page}){
 const count=page?state.attention?.pages?.[page]:section?state.attention?.sections?.[section]:state.attention?.unread;
 return count>0?<span className="a-attention-badge" role="status" aria-label={`${count} unread items`}>{count}</span>:null;
}
export function AttentionReview({state,act,page}){
 const items=(state.attention?.items||[]).filter(i=>i.page===page&&!i.read);
 if(!items.length)return null;
 return <div className="a-attention-review"><div className="a-settings-row"><span>{items.length} new {items.length===1?'item':'items'} to review</span><button type="button" className="a-link" data-action="attention.read" onClick={()=>act('attention.read',{ids:items.map(i=>i.id)})}>Mark reviewed</button></div>{page!=='updates'&&items.map(item=><p key={item.id}><strong>{item.title}</strong>{item.detail&&<> · {item.detail}</>}</p>)}</div>;
}
