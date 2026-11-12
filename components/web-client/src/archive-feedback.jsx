import React,{createContext,useContext,useEffect,useRef} from 'react';
import {Archive,ArchiveRestore,X} from 'lucide-react';
import {createNoticeExpiry} from './notice-expiry.js';

export const ArchiveContext=createContext(null);
export function ArchiveButton({chat,archived=false,children,...props}){
 const archive=useContext(ArchiveContext),busy=archive?.pending.includes(chat.id),blocked=archive?.pending.length>0;
 return <button type="button" {...props} data-action={archived?'session.restore':'session.archive'} aria-disabled={blocked||undefined} aria-busy={busy||undefined} onClick={()=>{if(!blocked)archive.run(chat,!archived)}}>{archived?<ArchiveRestore/>:<Archive/>}{busy?'Saving…':children|| (archived?'Restore':'Archive')}</button>;
}
export function ArchiveNotice({value}){
 const expiry=useRef(null),region=useRef(null);
 const operation=value.notice?.operation,phase=value.notice?.phase,persistent=value.notice?.persistent,navigationPending=value.notice?.navigationPending;
 const kept=value.notice?.kept===true;
 useEffect(()=>{
  if(phase!=='success'||navigationPending||persistent||kept||!Number.isSafeInteger(operation))return;
  const doc=region.current?.ownerDocument||globalThis.document;
  const timer=createNoticeExpiry(()=>value.expire(operation));expiry.current=timer;
  const visibility=()=>timer.pause('hidden',!!doc?.hidden);
  visibility();
  timer.pause('hover',!!region.current?.matches(':hover'));
  timer.pause('focus',!!region.current?.contains(doc?.activeElement));
  doc?.addEventListener('visibilitychange',visibility);
  return()=>{timer.dispose();expiry.current=null;doc?.removeEventListener('visibilitychange',visibility)};
 },[operation,phase,navigationPending,persistent,kept,value.expire]);
 const notice=value.notice;if(!notice)return null;
 return <div ref={region} className="a-alert" style={notice.phase==='error'?undefined:{color:'var(--a-ink)',background:'var(--a-soft)',borderColor:'var(--a-line)'}} data-archive-notice role={notice.phase==='error'?'alert':'status'} aria-live={notice.phase==='error'?'assertive':'polite'} aria-atomic="true"
  onMouseEnter={()=>expiry.current?.pause('hover',true)} onMouseLeave={()=>expiry.current?.pause('hover',false)}
  onFocusCapture={()=>expiry.current?.pause('focus',true)} onBlurCapture={event=>{if(!event.currentTarget.contains(event.relatedTarget))expiry.current?.pause('focus',false)}}>
  <span>{notice.text}{notice.title&&<> · {notice.title}</>}</span>
  <div className="a-alert-actions">
  {notice.phase==='success'&&notice.archive&&<button type="button" className="a-link" data-action="session.restore" disabled={value.pending.includes(notice.id)} onClick={()=>value.run({id:notice.id,title:notice.title},false)}>Undo</button>}
  {notice.phase==='success'&&!persistent&&<button type="button" className="a-link" aria-pressed={kept} onClick={()=>value.keep(operation,!kept)}>{kept?'Keeping open':'Keep open'}</button>}
  {notice.phase!=='pending'&&<button type="button" className="a-icon" aria-label="Dismiss archive notification" onClick={()=>value.dismiss(operation)}><X/></button>}
  </div>
 </div>;
}