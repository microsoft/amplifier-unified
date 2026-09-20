import React,{useEffect,useRef,useState} from 'react';
import './coordination.css';

const keyOf=target=>JSON.stringify([target.sessionId,target.workerId||null]);
const labels={idle:'Ready for follow-up',working:'Working',running:'Working',starting:'Starting',queued:'Queued',stopping:'Interruption requested',completed:'Completed',interrupted:'Outcome unknown',error:'Needs attention',cancelled:'Cancelled',stopped:'Stopped'};
const empty={selected:[],cursors:{},results:{},drafts:{}};

export function CoordinationPanel({dispatch}){
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
