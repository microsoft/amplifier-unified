import React from 'react';
import {AlertCircle,Clock3,CircleHelp} from 'lucide-react';
import './dependency-waiting.css';

// Host-derived request relationships only. Expanding is private presentation;
// following a link uses the normal selection action and never sends a draft.
export function DependencyWaiting({activity,sessionId,act}){
 if(!sessionId||activity?.sessionId!==sessionId)return null;
 const waiting=activity.waiting;
 if(!waiting?.targets?.length)return null;
 const pending=!waiting.phase||waiting.phase==='pending';
 const Icon=activity.kind==='attention'?(waiting.phase==='decision'?CircleHelp:AlertCircle):Clock3;
 const link=row=><>{row.available===false?<span>{row.title} (unavailable)</span>:<button type="button" className="a-link" data-action="session.select" onClick={()=>act('session.select',{id:row.sessionId})}>{row.title}</button>}{row.phase&&row.phase!=='pending'&&<span> — {row.label}</span>}{['error','failed','stopped','cancelled','interrupted','paused'].includes(row.status)&&<span> — chat {row.status}</span>}</>;
 return <section key={sessionId} className="a-dependency-waiting" aria-label="Chat dependencies">
  <Icon aria-hidden="true"/>
  {waiting.targets.length===1&&!waiting.hasMore?<span>{pending?'Waiting for ':''}{link(waiting.targets[0])}</span>:<details>
   <summary>{pending?(waiting.hasMore?'Waiting for other chats':`Waiting for ${waiting.targets.length} chats`):(waiting.hasMore?'Chat dependencies':`${waiting.targets.length} chat dependencies`)}</summary>
   <ul>{waiting.targets.map(row=><li key={row.sessionId}>{link(row)}</li>)}</ul>
   {waiting.hasMore&&<p>Showing the first saved dependency references. More references are not shown.</p>}
  </details>}
 </section>;
}
