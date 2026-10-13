import React from 'react';
import {ChatPlan} from './chat-plan';
import {sessionStatus} from './session-status';
import {FileText,FolderOpen,Globe,GitBranch,Activity,Paperclip,Folder} from 'lucide-react';
import {SavedArtifacts,chatArtifacts} from './canvas-library';
import {SubagentHistoryButton} from './subagent-history';
export function ChatOverview({state,act,changeDraft}){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId),outputs=chatArtifacts(state);
 const sources=[...new Map((session?.messages||[]).flatMap(row=>row.attachments||[]).map(row=>[row.id||row.name,row])).values()];
 const workers=session?.workers||[],activeWorkers=workers.filter(row=>['queued','starting','working','running','stopping'].includes(row.status)),working=activeWorkers.length,finished=workers.filter(row=>!activeWorkers.includes(row));
 const total=session?.workerSummary?.total??workers.length,finishedCount=session?.workerSummary?.finished??finished.length,recent=session?.workerSummary?.recentFinished??finished.slice(-5);
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 return <div className="a-chat-overview" role="region" aria-label="Chat overview" tabIndex={0}>
  <ChatPlan session={session} state={state} act={act} placement="overview"/>
  <section><div className="a-overview-title"><FileText/><h3>Outputs</h3><span>{outputs.length}</span></div>{outputs.length?<SavedArtifacts state={state} act={act} compact/>:<p>Files and visuals from this chat will appear here.</p>}<div className="a-overview-actions"><button className="a-link" type="button" onClick={()=>changeDraft({open:true,browser:false,library:true})}><FolderOpen/>Open file</button><button className="a-link" type="button" onClick={()=>changeDraft({browser:true,open:false,library:true})}><Globe/>Open website</button></div></section>
  {!!sources.length&&<section><div className="a-overview-title"><Paperclip/><h3>Sources</h3></div>{sources.length?<ul>{sources.map(row=><li key={row.id||row.name}>{row.name||'Attachment'}</li>)}</ul>:<p>No attachments in the loaded messages.</p>}</section>}
  {!!total&&<section><div className="a-overview-title"><GitBranch/><h3>Agents</h3>{working>0&&<span>{working} working</span>}</div><ul>{activeWorkers.map(row=><li key={row.id}>{row.name||row.instruction||'Worker'} <small>{row.status}</small></li>)}</ul><details className="a-overview-history"><summary>{finishedCount} finished observations</summary><ul>{recent.map(row=><li key={row.id}>{row.name||'Worker'} · {row.status}</li>)}</ul><SubagentHistoryButton state={state} session={session} act={act}/><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{panel:'coordination'}})}>View tasks and workers</button></details></section>}
  {workspace&&<section><div className="a-overview-title"><Folder/><h3>Workspace</h3></div><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'chats'}})}>{workspace.name}</button></section>}
  <section><div className="a-overview-title"><Activity/><h3>Activity</h3></div><p>{sessionStatus(session).label}</p><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{panel:'session-details'}})}>Chat info</button></section>
 </div>;
}
