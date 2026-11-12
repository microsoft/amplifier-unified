import React from 'react';
import {AlertCircle,Clock3,CircleHelp} from 'lucide-react';
import {neutralDependencyPhase as neutral,adverseDependencyStatus as interrupted,pendingDependency} from './navigation-presentation';
import './dependency-waiting.css';

// Host-derived request relationships only. Expanding is private presentation;
// following a link uses the normal selection action and never sends a draft.
export function DependencyWaiting({activity,sessionId,act}){
 if(!sessionId||activity?.sessionId!==sessionId)return null;
 const waiting=activity.waiting;
 if(!waiting?.targets?.length)return null;
 return <section key={sessionId} className="a-dependency-waiting" aria-label="Chat dependencies"><DependencyWaitingContent activity={activity} act={act}/></section>;
}

// Also used by the navigation flyout, without the conversation column geometry.
export function DependencyWaitingContent({activity,act}){
 const waiting=activity?.waiting;
 if(!waiting?.targets?.length)return null;
 const pending=pendingDependency(activity);
 const aggregate=!pending&&(waiting.label||activity.label||waiting.phase);
 const targetLabels=row=>[!neutral(row.phase)&&(row.label||row.phase),interrupted(row.status)&&`chat ${row.status}`].filter(Boolean);
 const visibleLabels=[...new Set([aggregate,...waiting.targets.flatMap(targetLabels)].filter(Boolean))];
 const Icon=activity.kind==='attention'?(waiting.phase==='decision'?CircleHelp:AlertCircle):Clock3;
 const link=row=><span className="a-dependency-target">
  {row.available===false?<span className="a-dependency-name" title={row.title}>{row.title}</span>:<button type="button" className="a-link a-dependency-name" title={row.title} aria-label={row.title} data-action="session.select" onClick={()=>act('session.select',{id:row.sessionId})}>{row.title}</button>}
  {row.available===false&&<span> (unavailable)</span>}
  {!neutral(row.phase)&&<span> — {row.label||row.phase}</span>}
  {interrupted(row.status)&&<span> — chat {row.status}</span>}
 </span>;
 return <div className="a-dependency-content">
  <Icon aria-hidden="true"/>
  {waiting.targets.length===1&&!waiting.hasMore?<div className="a-dependency-single">{pending&&<span className="a-dependency-prefix">Waiting on </span>}{link(waiting.targets[0])}{aggregate&&!targetLabels(waiting.targets[0]).includes(aggregate)&&<span> — {aggregate}</span>}</div>:<details>
   <summary>{pending?`Waiting on ${waiting.targets.length}${waiting.hasMore?'+':''} chats`:<>{visibleLabels.join(' · ')}{visibleLabels.length>0?' — ':''}{waiting.hasMore?'Chat dependencies':`${waiting.targets.length} chat dependencies`}</>}</summary>
   <ul>{waiting.targets.map(row=><li key={row.sessionId}>{link(row)}</li>)}</ul>
   {waiting.hasMore&&<p>Showing the first saved dependency references. More references are not shown.</p>}
  </details>}
 </div>;
}
