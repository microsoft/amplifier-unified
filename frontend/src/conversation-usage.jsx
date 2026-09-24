import React,{useEffect,useRef,useState} from 'react';
import {usageMetric,usageExport} from './conversation-usage.js';

export function ConversationUsage({sessionId,act}){
 const [snapshot,setSnapshot]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState(false);
 const generation=useRef(0);
 async function refresh(){
  const ticket=++generation.current;setBusy(true);setError('');setCopied(false);
  try{
   const response=await act('capacity.read',{sessionId,limit:1});
   if(response?.accepted===false||response?.result?.usage?.sessionId!==sessionId)throw Error('Usage could not be read.');
   if(ticket===generation.current)setSnapshot(response.result);
  }catch(error){if(ticket===generation.current)setError(error.message||'Usage could not be read.')}
  finally{if(ticket===generation.current)setBusy(false)}
 }
 useEffect(()=>{setSnapshot(null);refresh();return()=>{generation.current++}},[sessionId]);
 const current=snapshot?.usage?.sessionId===sessionId?snapshot:null,usage=current?.usage,metrics=usage?.metrics||{};
 async function copy(){try{await navigator.clipboard.writeText(JSON.stringify(usageExport(current),null,2));setCopied(true)}catch{setError('Clipboard unavailable. Expand the usage data to select and copy it.')}}
 return <section aria-label="Conversation usage" className="a-settings-section">
  <h3>Conversation usage</h3>
  <p className="a-caption">Recorded model calls in this chat and its observed workers. Cumulative tokens are not the current context-window size.</p>
  {current&&<><p>{usage.calls} recorded model calls · Snapshot {new Date(current.observedAt*1000).toLocaleTimeString()}</p>
   <dl className="a-capability-counts"><div><dt>Total tokens including cache writes</dt><dd>{usageMetric(metrics.grossTotalTokens)}</dd></div><div><dt>Cost (USD)</dt><dd>{usageMetric(metrics.costUsd,true)}</dd></div></dl>
   <details><summary>Token and model breakdown</summary>
    <dl>{[['inputTokens','Input tokens (includes cache reads)'],['outputTokens','Output tokens'],['reasoningTokens','Reasoning tokens (part of output)'],['cacheReadTokens','Cache-read tokens'],['cacheWriteTokens','Cache-write tokens']].map(([key,label])=><div key={key}><dt>{label}</dt><dd>{usageMetric(metrics[key])}</dd></div>)}</dl>
    {usage.providers.map(row=><p key={row.provider+'|'+row.model}>{row.provider} / {row.model} · {row.calls} calls · {usageMetric(row.metrics.grossTotalTokens)} tokens · {usageMetric(row.metrics.costUsd,true)}</p>)}
   </details>
   <p className="a-caption">Historical calls without telemetry and provider-internal retries may be missing. Unavailable values are not zero.{usage.excludedUnboundCalls>0?` ${usage.excludedUnboundCalls} records could not be bound to this chat and are excluded.`:''}</p>
  </>}
  <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="capacity.read" disabled={busy} onClick={refresh}>{busy?'Reading usage…':'Refresh usage'}</button>{current&&<button type="button" className="a-soft" onClick={copy}>Copy usage</button>}</div>
  {current&&<details><summary>Usage data</summary><pre className="a-state-view">{JSON.stringify(usageExport(current),null,2)}</pre></details>}
  {copied&&<p role="status">Usage copied</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
