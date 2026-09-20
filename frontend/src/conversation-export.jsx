import React from 'react';
import {Copy,Download} from 'lucide-react';

export function ConversationExport({session,state,act}){
 const report=state.view?.conversationExport;
 const current=report?.sessionId===session.id?report:null;
 const busy=current?.status==='pending';
 return <div><p className="a-caption">Export the whole conversation as Markdown, including messages outside the loaded page. Voice exchanges and artifact references are included; tool payloads are omitted.</p><div className="a-dialog-actions">
  <button type="button" className="a-soft" data-action="session.export" disabled={busy} onClick={()=>act('session.export',{id:session.id,format:'markdown',destination:'clipboard'})}><Copy/>Copy Markdown</button>
  <button type="button" className="a-soft" data-action="session.export" disabled={busy} onClick={()=>act('session.export',{id:session.id,format:'markdown',destination:'download'})}><Download/>Download Markdown</button>
  <button type="button" className="a-link" data-action="session.export" onClick={()=>act('session.export',{id:session.id})}>Export JSON</button>
 </div>{current&&<p role={current.status==='error'?'alert':'status'}>{busy?'Preparing conversation export…':current.message}</p>}</div>;
}
