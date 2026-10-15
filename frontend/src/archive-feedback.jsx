import React,{createContext,useContext} from 'react';
import {Archive,ArchiveRestore,X} from 'lucide-react';

export const ArchiveContext=createContext(null);
export function ArchiveButton({chat,archived=false,children,...props}){
 const archive=useContext(ArchiveContext),busy=archive?.pending.includes(chat.id);
 return <button type="button" {...props} data-action={archived?'session.restore':'session.archive'} disabled={busy} aria-busy={busy||undefined} onClick={()=>archive.run(chat,!archived)}>{archived?<ArchiveRestore/>:<Archive/>}{busy?archived?'Restoring…':'Archiving…':children|| (archived?'Restore':'Archive')}</button>;
}
export function ArchiveNotice({value}){
 const notice=value.notice;if(!notice)return null;
 return <div className="a-alert" data-archive-notice role={notice.phase==='error'?'alert':'status'} aria-live={notice.phase==='error'?'assertive':'polite'} aria-atomic="true">
  <span>{notice.text}{notice.title&&<> · {notice.title}</>}</span>
  {notice.phase==='success'&&notice.archive&&<button type="button" className="a-link" data-action="session.restore" disabled={value.pending.includes(notice.id)} onClick={()=>value.run({id:notice.id,title:notice.title},false)}>Undo</button>}
  {notice.phase!=='pending'&&<button type="button" className="a-icon" aria-label="Dismiss archive notification" onClick={value.dismiss}><X/></button>}
 </div>;
}