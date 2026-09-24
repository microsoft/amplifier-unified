import React from 'react';
import {FileText,FolderOpen,Globe,GitBranch,Activity,Paperclip} from 'lucide-react';
import {SavedArtifacts,chatArtifacts} from './canvas-library';
import {SubagentHistoryButton} from './subagent-history';
export function ChatOverview({state,act,changeDraft}){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId),outputs=chatArtifacts(state);
 const sources=[...new Map((session?.messages||[]).flatMap(row=>row.attachments||[]).map(row=>[row.id||row.name,row])).values()];
 const workers=session?.workers||[],working=workers.filter(row=>['queued','starting','working','running','stopping'].includes(row.status)).length;
 return <div className="a-chat-overview">
  <section><div className="a-overview-title"><FileText/><h3>Outputs</h3><span>{outputs.length}</span></div>{outputs.length?<SavedArtifacts state={state} act={act}/>:<p>Files and visuals from this chat will appear here.</p>}<div className="a-overview-actions"><button className="a-link" type="button" onClick={()=>changeDraft({open:true,browser:false,library:true})}><FolderOpen/>Open file</button><button className="a-link" type="button" onClick={()=>changeDraft({browser:true,open:false,library:true})}><Globe/>Open website</button></div></section>
  <section><div className="a-overview-title"><Paperclip/><h3>Sources</h3></div>{sources.length?<ul>{sources.map(row=><li key={row.id||row.name}>{row.name||'Attachment'}</li>)}</ul>:<p>No attachments in the loaded messages.</p>}</section>
  <section><div className="a-overview-title"><GitBranch/><h3>Agents</h3>{working>0&&<span>{working} working</span>}</div>{workers.length?<ul>{workers.map(row=><li key={row.id}>{row.name||row.instruction||'Worker'} <small>{row.status}</small></li>)}</ul>:<p>Delegated work will appear here.</p>}<SubagentHistoryButton state={state} session={session} act={act}/><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{panel:'coordination'}})}>View tasks and workers</button></section>
  <section><div className="a-overview-title"><Activity/><h3>Activity</h3></div><p>{session?.status||'Idle'}</p><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{panel:'runtime'}})}>Chat controls and details</button></section>
 </div>;
}
