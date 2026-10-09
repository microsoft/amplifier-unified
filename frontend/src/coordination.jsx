import React,{useEffect,useRef,useState} from 'react';
import './coordination.css';

const keyOf=target=>JSON.stringify([target.sessionId,target.workerId||null]);
const labels={idle:'Ready for follow-up',working:'Working',running:'Working',starting:'Starting',queued:'Queued',stopping:'Interruption requested',completed:'Completed',interrupted:'Outcome unknown',error:'Needs attention',cancelled:'Cancelled',stopped:'Stopped'};
const empty={selected:[],cursors:{},results:{},drafts:{}};

export function CoordinationPanel({dispatch,sessionId}){
 // Session storage is isolated by origin and tab; client IDs rotate on reload.
 const storageKey='amplifier-coordination-v1';
 const [saved,setSaved]=useState(()=>{try{return {...empty,...JSON.parse(sessionStorage.getItem(storageKey)||'null')}}catch{return empty}});
 const inflight=useRef(new Set());
 const savedRef=useRef(saved),[items,setItems]=useState([]),[error,setError]=useState(''),[waitError,setWaitError]=useState(''),[notice,setNotice]=useState(''),[working,setWorking]=useState(null),[waiting,setWaiting]=useState(false);
 const write=update=>{const next=update(savedRef.current);savedRef.current=next;setSaved(next);try{sessionStorage.setItem(storageKey,JSON.stringify(next))}catch{setNotice('This browser could not save delivery cursors. Reports may appear again after reload.')}};
 const refresh=async()=>{try{const response=await dispatch('coordination.list',{}, {feedback:false});setItems(response.result.items);if(response.result.truncated)setNotice('Showing the first 100 targets. Agents can address any known conversation directly.')}catch(error){setError(error.message)}};
 useEffect(()=>{refresh()},[]);
 useEffect(()=>{
  if(!saved.selected.length)return;
  let alive=true,retries=0;const controller=new AbortController();
  (async()=>{
   while(alive){
    const targets=saved.selected.map(target=>({...target,...(savedRef.current.cursors[keyOf(target)]?{afterCursor:savedRef.current.cursors[keyOf(target)]}:{})}));
    try{
     setWaiting(true);
     const response=await dispatch('coordination.wait',{targets,waitMs:30000,maxBytes:32768},{feedback:false,signal:controller.signal});
     if(!alive)return;
     retries=0;setWaitError('');
     const result=response.result;
     if(result.errors.length){setWaitError(result.errors.map(row=>row.error).join(' '));return}
     write(previous=>{
      const cursors={...previous.cursors},results={...previous.results};
      for(const row of result.targets){
       const key=keyOf(row.target),retained=results[key]||[],known=new Set(retained.map(item=>item.id));
       results[key]=[...retained,...row.results.filter(item=>!known.has(item.id)).map(item=>({...item,text:item.text.slice(0,4096),textTruncated:item.textTruncated||item.text.length>4096}))].slice(-8);
       cursors[key]=row.nextCursor;
      }
      const keep=new Set([...Object.keys(results).slice(-16),...previous.selected.map(keyOf)]);
      return {...previous,cursors:Object.fromEntries(Object.entries(cursors).filter(([key])=>keep.has(key))),results:Object.fromEntries(Object.entries(results).filter(([key])=>keep.has(key)))};
     });
     setItems(previous=>previous.map(item=>result.targets.find(row=>keyOf(row.target)===keyOf(item.target))||item));
     if(result.targets.some(row=>row.cursorGap))setNotice('Some older reports are no longer retained here. Open the saved conversation for its available history.');
    }catch(error){if(!alive||error.name==='AbortError')return;setWaitError('Waiting to reconnect: '+error.message);await new Promise(resolve=>{const done=()=>{clearTimeout(timer);controller.signal.removeEventListener('abort',done);resolve()};const timer=setTimeout(done,Math.min(15000,1000*2**retries++));controller.signal.addEventListener('abort',done,{once:true})})}
    finally{if(alive)setWaiting(false)}
   }
  })();
  return()=>{alive=false;controller.abort()};
 },[JSON.stringify(saved.selected)]);
 const toggle=target=>write(previous=>{const key=keyOf(target),exists=previous.selected.some(row=>keyOf(row)===key);return {...previous,selected:exists?previous.selected.filter(row=>keyOf(row)!==key):[...previous.selected,target].slice(0,8)}});
 const control=async(item,action)=>{
  const key=keyOf(item.target);if(inflight.current.has(key))return;inflight.current.add(key);
  const text=savedRef.current.drafts[key]||'';setWorking(key);setError('');
  try{
   const response=await dispatch(action,{...item.target,...(action==='coordination.followup'?{text}:{})});
   if(response.delivery==='unknown'){setError('Delivery is unknown. Work was not replayed. Inspect the target before sending another instruction.');return}
   if(action==='coordination.followup')write(previous=>({...previous,drafts:{...previous.drafts,[key]:''}}));
   setNotice(action==='coordination.interrupt'?'Interruption requested. Effects may already have happened.':'Follow-up accepted.');
   await refresh();
  }catch(error){setError(error.message)}finally{inflight.current.delete(key);setWorking(null)}
 };
 return <div className="a-coordination">
  {sessionId&&<RelatedWork dispatch={dispatch} sessionId={sessionId}/>}
  <p>Follow up on a conversation or its workers without switching away from your current draft. Watch up to eight targets for reports or requests for attention.</p>
  <div className="a-dialog-actions"><button className="a-soft" data-action="coordination.list" onClick={refresh}>Refresh targets</button><button className="a-link" disabled={!saved.selected.length} onClick={()=>write(previous=>({...previous,selected:[]}))}>Stop watching</button><span role="status">{waiting?'Waiting for updates…':saved.selected.length?`${saved.selected.length} targets watched`:'Choose targets to watch'}</span></div>
  {error&&<p role="alert" className="a-alert">{error}</p>}{waitError&&<p role="alert" className="a-alert">{waitError}</p>}{notice&&<p role="status">{notice}</p>}
  {items.map(item=>{const key=keyOf(item.target),selected=saved.selected.some(row=>keyOf(row)===key),reports=saved.results[key]||[];return <section key={key} className="a-coordination-target" aria-label={`${item.kind==='worker'?'Worker':'Conversation'}: ${item.title}`}>
   <label><input type="checkbox" checked={selected} disabled={!selected&&saved.selected.length>=8} onChange={()=>toggle(item.target)}/><strong>{item.title}</strong> · {item.kind==='worker'?'Worker':'Conversation'}</label>
   <p className="a-caption">{labels[item.status]||item.status}{item.attention?' · Needs attention':''}</p>
   {reports.map(report=><article key={report.id} data-report-id={report.id}><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{report.text}</p>{report.textTruncated&&<small>Report excerpt. Open the saved conversation for more.</small>}</article>)}
   <details><summary>Follow up</summary><form onSubmit={event=>{event.preventDefault();control(item,'coordination.followup')}}>
    <label>Follow-up for {item.title}<textarea value={saved.drafts[key]||''} disabled={!item.canFollowup||working===key} onChange={event=>write(previous=>({...previous,drafts:{...previous.drafts,[key]:event.target.value}}))}/></label>
    <div className="a-dialog-actions"><button className="a-soft" data-action="coordination.followup" disabled={!item.canFollowup||!saved.drafts[key]?.trim()||working===key}>Send follow-up</button></div>
   </form></details><button type="button" className="a-link a-danger" data-action="coordination.interrupt" disabled={!item.canInterrupt||working===key} onClick={()=>control(item,'coordination.interrupt')}>Interrupt</button>
  </section>})}
 </div>;
}

export function RelatedWork({dispatch,sessionId}){
 const [items,setItems]=useState([]),[context,setContext]=useState({grants:[],requests:[],proposals:[]}),[error,setError]=useState(''),[notice,setNotice]=useState(''),[inspected,setInspected]=useState({});
 const [peer,setPeer]=useState(''),[grantId,setGrantId]=useState(''),[purpose,setPurpose]=useState(''),[text,setText]=useState(''),[title,setTitle]=useState(''),[mode,setMode]=useState('notify'),[idleStart,setIdleStart]=useState(false),[allowCreate,setAllowCreate]=useState(false),[busy,setBusy]=useState(false);
 const refresh=async()=>{
  const [state,list]=await Promise.all([dispatch('coordination.context',{sessionId},{feedback:false}),dispatch('coordination.list',{sessionId},{feedback:false})]);
  setContext(state.result);
  const workspace=list.result.items.find(row=>row.target.sessionId===sessionId)?.workspace;
  const peers=await dispatch('coordination.list',{workspace,limit:50,includeWorkers:false},{feedback:false});
  setItems(peers.result.items.filter(row=>row.kind==='conversation'&&row.target.sessionId!==sessionId));
 };
 useEffect(()=>{setInspected({});refresh().catch(error=>setError(error.message))},[sessionId]);
 const run=async(action,args)=>{
  if(busy)return;setBusy(true);setError('');
  try{
   const response=await dispatch(action,args,{feedback:false});
   if(response.accepted===false&&response.delivery!=='denied'){setError(response.result?.reason||'This capability is unsupported.');return}
   if(action==='coordination.grant'||action==='coordination.decide'&&response.accepted)setGrantId(response.result.id);
   setNotice(response.delivery==='unknown'?'Outcome unknown; work will not be replayed.':action==='coordination.create'?'Task chat retained. Admission and final qualification are separate.':response.delivery?`Peer message: ${response.delivery}`:action==='coordination.revoke'?'Grant revoked. Saved messages remain available.':'Collaboration authorized.');
   await refresh();
  }catch(error){setError(error.message)}finally{setBusy(false)}
 };
 const current=context.grants.filter(row=>!row.revoked);
 const selectedGrant=current.find(row=>row.id===grantId);
 const canCreate=selectedGrant?.allowCreate&&selectedGrant?.idleStart&&selectedGrant?.modes.includes('queue')&&context.continuation?.supported;
 const inspectResult=async requestId=>{try{const value=await dispatch('coordination.result',{requestId},{feedback:false});setInspected(previous=>({...previous,[requestId]:value.result}))}catch(error){setError(error.message)}};
 const reveal=(sid,messageId)=>dispatch('message.reveal',{sessionId:sid,messageId}).catch(error=>setError(error.message));
 const openArtifact=async(sid,reference)=>{
  const path=reference.replace(/@sha256:[a-f0-9]{64}$/,'');
  const workspace=sid===sessionId?context.workspace:items.find(row=>row.target.sessionId===sid)?.workspace;
  try{await dispatch('session.select',{id:sid});await dispatch('canvas.openFile',{sessionId:sid,workspace,path})}catch(error){setError(error.message)}
 };
 return <section className="a-coordination-related" aria-label="Related work">
  <h3>Related work</h3>
  <p className="a-caption">Authorize this task once. Notify, idle queue and generation-anchored steering keep your current chat and draft. Saved waits resume once after a declared result is sealed; verify its artifact independently.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <label>Peer conversation<select value={peer} onChange={event=>setPeer(event.target.value)}><option value="">Choose a peer</option>{items.map(row=><option key={row.target.sessionId} value={row.target.sessionId}>{row.title}</option>)}</select></label>
  <details><summary>Authorize collaboration</summary><form onSubmit={event=>{event.preventDefault();run('coordination.grant',{sessionId,participants:[peer],purpose,modes:['notify','queue','steer'],idleStart,allowCreate})}}>
   <label>Collaboration purpose<textarea aria-label="Collaboration purpose" value={purpose} onChange={event=>setPurpose(event.target.value)}/></label>
   <label><input type="checkbox" checked={idleStart} onChange={event=>{setIdleStart(event.target.checked);if(!event.target.checked)setAllowCreate(false)}}/>Allow necessary idle starts</label>
   <label><input type="checkbox" checked={allowCreate} disabled={!idleStart} onChange={event=>setAllowCreate(event.target.checked)}/>Allow durable task chats</label>
   {!idleStart&&<p className="a-caption">Task creation and saved waits need explicit idle starts and queue mode. Notify does not run work.</p>}
   <button className="a-soft" disabled={busy||!peer||!purpose.trim()}>Authorize task collaboration</button>
  </form></details>
  {(context.proposals||[]).map(proposal=><article key={proposal.proposalId} data-proposal-id={proposal.proposalId}>
   <strong>Collaboration proposal · {proposal.delivery}</strong><p>{proposal.prompt||proposal.result.purpose}</p>
   {proposal.delivery==='awaiting_approval'&&<><p>Pending until a human decides this exact scope. No model starts when you review it.</p>
    <button className="a-soft" disabled={busy||proposal.sourceSessionId!==sessionId} onClick={()=>run('coordination.decide',{sessionId,proposalId:proposal.proposalId,decision:'allow'})}>Approve exact proposal</button>
    <button className="a-link" disabled={busy||proposal.sourceSessionId!==sessionId} onClick={()=>run('coordination.decide',{sessionId,proposalId:proposal.proposalId,decision:'deny'})}>Deny proposal</button></>}
  </article>)}
  <label>Current collaboration grant<select value={grantId} onChange={event=>setGrantId(event.target.value)}><option value="">Choose a grant</option>{current.map(row=><option key={row.id} value={row.id}>{row.purpose}</option>)}</select></label>
  <button className="a-link a-danger" disabled={busy||!grantId} onClick={()=>run('coordination.revoke',{sessionId,grantId})}>Revoke grant</button>
  <form onSubmit={event=>{event.preventDefault();run('coordination.send',{senderSessionId:sessionId,sessionId:peer,grantId,text,mode})}}>
   <label>Peer message<textarea aria-label="Peer message" value={text} onChange={event=>setText(event.target.value)}/></label>
   <label>Delivery mode<select value={mode} onChange={event=>setMode(event.target.value)}><option value="notify">Notify without waking</option><option value="queue">Queue at idle boundary</option><option value="steer">Steer current generation</option></select></label>
   <button className="a-soft" disabled={busy||!peer||!grantId||!text.trim()}>Send peer message</button>
  </form>
  <details><summary>Commission durable task</summary>
   <label>Task chat title<input aria-label="Task chat title" value={title} onChange={event=>setTitle(event.target.value)}/></label>
   <p className="a-caption">Uses the peer-message text above as its brief, your recorded configuration and a task output namespace. Requires task-creation, queue and idle-start scope.</p>
   <button className="a-soft" disabled={busy||!canCreate||!title.trim()||!text.trim()} onClick={()=>run('coordination.create',{senderSessionId:sessionId,grantId,title,text})}>Create task chat</button>
  </details>
  <button className="a-link" disabled={busy} onClick={()=>refresh().catch(error=>setError(error.message))}>Refresh related work</button>
  {items.filter(row=>row.collaboration).map(row=><div key={row.target.sessionId}>{row.title} · created by {items.find(item=>item.target.sessionId===row.collaboration.creatorSessionId)?.title||row.collaboration.creatorTitle||'Source conversation'} <button className="a-link" onClick={()=>dispatch('session.select',{id:row.target.sessionId})}>Open task chat</button><details><summary>Task identity</summary><code>{row.target.sessionId} · creator {row.collaboration.creatorSessionId}</code></details></div>)}
  {context.requests.map(row=>{const result=inspected[row.requestId],response=result?.receipt?.response||row.response;return <article key={row.requestId} data-request-id={row.requestId}>
   <small>{row.senderSessionId===sessionId?'Sent':'Received'} · {row.delivery} · {row.requestId}</small>
   <button className="a-link" onClick={()=>inspectResult(row.requestId)}>Inspect exact result</button>
   <button className="a-link" onClick={()=>reveal(row.target.sessionId,row.messageId)}>Open submitted message</button>
   {response&&<div data-part="exact-result"><p>Result: {response.status} · {response.outcome} · {response.qualified?'Qualified terminal evidence':'Not qualified'}</p><p>{response.text}</p>{response.detail&&<p role="alert">{response.detail}</p>}
    {response.terminalMessageId&&response.status==='sealed'&&<button className="a-link" data-message-id={response.terminalMessageId} onClick={()=>reveal(row.target.sessionId,response.terminalMessageId)}>Open exact terminal message</button>}
    {(response.references||[]).map(reference=><p key={reference}><code>{reference}</code>{/^[^@\n]+@sha256:[a-f0-9]{64}$/.test(reference)&&!reference.includes('://')&&<button className="a-link" onClick={()=>openArtifact(row.target.sessionId,reference)}>Open referenced artifact</button>}</p>)}
    {(response.messageIds||[]).map(messageId=><button key={messageId} className="a-link" onClick={()=>reveal(row.target.sessionId,messageId)}>Open evidence message {messageId}</button>)}
   </div>}{result&&<p className="a-caption">{result.detail}</p>}
  </article>})}
 </section>;
}
