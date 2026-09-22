import React,{useEffect,useReducer,useRef,useState} from 'react';
import './publishing.css';

// Only submitted requests survive closing this panel. They stay private to this
// browser page and are never written into conversation drafts or shared views.
const requests=new Map();
function requestState(sid){if(!requests.has(sid))requests.set(sid,{pending:null,busy:false,listeners:new Set()});return requests.get(sid)}
function notify(record){record.listeners.forEach(fn=>fn())}
const description=value=>typeof value==='string'?value:value?.message||JSON.stringify(value||'The action failed.');
const safeLink=value=>{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null}catch{return null}};

export function PublishingSettings({session,act}){
 const sid=session?.id,record=requestState(sid),[,render]=useReducer(value=>value+1,0);
 const [data,setData]=useState(null),[siteId,setSiteId]=useState(record.pending?.args.siteId||''),[sourcePath,setSourcePath]=useState(''),[releaseId,setReleaseId]=useState(record.pending?.args.releaseId||''),[note,setNote]=useState('');
 const [reading,setReading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[inspection,setInspection]=useState(null);
 const mounted=useRef(true),readFlight=useRef(false);
 useEffect(()=>{mounted.current=true;record.listeners.add(render);return()=>{mounted.current=false;record.listeners.delete(render)}},[record]);
 const update=fn=>{if(mounted.current)fn()};
 const sites=data?.sites||[],releases=data?.releases||[],selected=sites.find(row=>row.id===siteId),release=releases.find(row=>row.id===releaseId&&row.siteId===siteId);
 const busy=record.busy||reading,locked=busy||!!record.pending,siteReleases=releases.filter(row=>row.siteId===siteId);
 async function read(action,args={}){
  const response=await act(action,{sessionId:sid,...args});
  if(!response?.accepted)throw Error(response?.error||'The server did not confirm this request.');
  return response.result;
 }
 async function refresh({reconcile=false}={}){
  const value=await read('publishing.list');
  update(()=>setData(value));
  if(reconcile&&record.pending){
   const receipt=value.receipts?.find(row=>row.requestId===record.pending.args.requestId);
   if(receipt?.state==='unknown'){
    record.pending={...record.pending,receipt};notify(record);
    update(()=>setNotice('The saved receipt records an unknown outcome. Nothing was replayed.'));
   }else if(receipt&&['succeeded','failed'].includes(receipt.state)){
    record.pending=null;notify(record);
    update(()=>receipt.state==='failed'?setError(description(receipt.error)):setNotice('The saved receipt confirms this request succeeded.'));
   }
  }
  return value;
 }
 async function inspect(action='publishing.list',reconcile=true){
  if(record.busy||readFlight.current)return;
  readFlight.current=true;setReading(true);setError('');
  try{if(action==='publishing.list')await refresh({reconcile});else{setInspection({action,value:await read(action,{siteId})});if(action==='publishing.status')await refresh()}}
  catch(e){setError(e.message)}finally{readFlight.current=false;setReading(false)}
 }
 useEffect(()=>{if(sid)inspect('publishing.list',false)},[sid]);
 async function submit(action,args,retry=false){
  if(record.busy||readFlight.current||(!retry&&record.pending))return;
  const command=retry?record.pending:{action,args:{sessionId:sid,...args,requestId:crypto.randomUUID()}};
  if(!command)return;
  record.pending=command;record.busy=true;notify(record);setError('');setNotice('');
  try{
   const response=await act(command.action,command.args);
   if(!response?.accepted)throw Error(response?.error||'The connection ended before the outcome was confirmed.');
   const result=response.result;
   if(!result||(command.action==='publishing.build'?!result.id:!['succeeded','failed','unknown','running'].includes(result.state)))throw Error('The server returned no complete publishing receipt.');
   if(result?.state==='unknown'||result?.state==='running'){
    update(()=>setError(description(result.error||'The outcome is not confirmed. Inspect receipts before continuing.')));
   }else if(result?.state==='failed'){
    record.pending=null;
    update(()=>setError(description(result.error)));
   }else{
    record.pending=null;
    update(()=>{setNotice(command.action.slice(11)+' completed.');if(command.action==='publishing.build'){setSiteId(result.siteId);setReleaseId(result.id);setNote('')}});
   }
   try{await refresh()}catch(e){update(()=>setError('The action returned, but the list could not refresh. '+e.message))}
  }catch(e){
   // A definite rejection has no unknown side effect. A lost response retains
   // the exact command and request identity for an explicit idempotent retry.
   if(e.status>=400&&e.status<500&&e.status!==408){
    record.pending=null;update(()=>setError(e.message));
    try{await refresh()}catch{}
   }else update(()=>setError(e.message+' Keep this request unchanged until its outcome is known.'));
  }finally{record.busy=false;notify(record)}
 }
 function acknowledgeUnknown(){if(record.busy||record.pending?.receipt?.state!=='unknown')return;record.pending=null;notify(record);setError('');setNotice('Unknown outcome acknowledged. Inspect the site and explicitly stop or remove it before deploying again.')}
 function chooseSite(value){setSiteId(value);setReleaseId('');setNote('');setInspection(null)}
 if(!sid)return <p>Choose a conversation to manage its published sites.</p>;
 return <section className="a-publishing" aria-label="Managed publishing">
  <h3>Publishing</h3><p>Save a built site as an immutable release, preview it, record your review, and explicitly deploy that version.</p>
  <div className="a-selection-card" aria-label="Publishing target"><strong>{data?.target?.host?.label||'Local server'}</strong><p>Access: {data?.target?.accessPolicy||'loopback-only'}</p><p>{data?.target?.detail||'URLs are reachable only on this server.'}</p><p className="a-caption">If Unified runs on another host, these URLs open on that host. No public hosting target is configured here.</p></div>
  <button type="button" className="a-soft" data-action="publishing.list" disabled={busy} onClick={()=>inspect()}>Inspect publishing</button>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {record.pending&&<div className="a-selection-card" aria-label="Unconfirmed publishing request"><strong>{record.busy?'Request in progress':record.pending.receipt?.state==='unknown'?'Recorded unknown outcome':'Request outcome unconfirmed'}</strong><p>{record.pending.action} · {record.pending.args.siteId||record.pending.args.releaseId}</p><code>{record.pending.args.requestId}</code><p>The exact submitted arguments are retained while this browser page stays open. Inspect publishing to check the saved receipt, or retry this same request. Nothing is retried automatically.</p>{record.pending.receipt?.state==='unknown'?<><p>{description(record.pending.receipt.error)}</p><button type="button" className="a-soft" disabled={busy} onClick={acknowledgeUnknown}>Acknowledge unknown outcome</button><p>Acknowledgement dismisses this pending request only. It does not repeat or resolve the operation; any site marked unknown requires an explicit stop or remove action.</p></>:<button type="button" className="a-soft" disabled={busy} data-action={record.pending.action} onClick={()=>submit(null,null,true)}>Retry exact request</button>}</div>}
  <h4>Sites</h4>{data&&!sites.length&&<p>No sites yet. Capture a release below to get started.</p>}
  <div className="a-publishing-sites">{sites.map(site=><button type="button" className="a-selection-card" key={site.id} disabled={locked} aria-pressed={siteId===site.id} onClick={()=>chooseSite(site.id)}><strong>{site.id}</strong><span>{site.status} · revision {site.revision}</span></button>)}</div>
  <h4>Capture built output</h4><p>Build with your existing project tools first, then enter its output folder relative to this task’s execution folder. Capturing preserves the current files as a new release.</p>
  <form onSubmit={e=>{e.preventDefault();submit('publishing.build',{siteId:siteId.trim(),sourcePath:sourcePath.trim()})}}>
   <label htmlFor="publishing-site">Site ID</label><input id="publishing-site" value={siteId} disabled={locked} maxLength={63} pattern="[a-z0-9][a-z0-9-]{0,62}" onChange={e=>chooseSite(e.target.value)} placeholder="my-site"/>
   <label htmlFor="publishing-source">Built output folder</label><input id="publishing-source" value={sourcePath} disabled={locked} onChange={e=>setSourcePath(e.target.value)} placeholder="dist"/>
   <button className="a-primary" data-action="publishing.build" disabled={locked||!siteId.trim()||!sourcePath.trim()}>Capture release</button>
  </form>
  {siteId&&<><h4>Saved releases for {siteId}</h4><label htmlFor="publishing-release">Release to preview, review, or deploy</label><select id="publishing-release" value={release?.id||''} disabled={locked} onChange={e=>{setReleaseId(e.target.value);setNote('')}}><option value="">Choose a saved release</option>{siteReleases.map(row=><option key={row.id} value={row.id}>{row.createdAt} · {row.id}</option>)}</select></>}
  {release&&<div className="a-selection-card" aria-label="Selected release"><h4>Exact release</h4><dl><dt>Release ID</dt><dd>{release.id}</dd><dt>Manifest digest</dt><dd>{release.manifestDigest}</dd><dt>Files</dt><dd>{release.files?.length||0} · {release.totalBytes||0} bytes</dd><dt>Preview</dt><dd>{release.previewStatus||'none'}</dd><dt>Review</dt><dd>{release.review?`Reviewed: ${release.review.note}`:'Not reviewed'}</dd></dl>
   <details><summary>Release files</summary><ul>{release.files?.map(file=><li key={file.path}><strong>{file.path}</strong> · {file.size} bytes<br/><code>{file.sha256}</code></li>)}</ul></details>
   <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={locked} data-action="publishing.preview" onClick={()=>submit('publishing.preview',{releaseId:release.id})}>Start preview</button>{release.previewStatus==='running'&&safeLink(release.previewUrl)&&<a href={safeLink(release.previewUrl)} target="_blank" rel="noopener noreferrer">Open exact release preview</a>}</div>
   <form onSubmit={e=>{e.preventDefault();submit('publishing.review',{releaseId:release.id,note:note.trim()})}}><label htmlFor="publishing-note">Review note for this exact release</label><textarea id="publishing-note" value={note} disabled={locked} maxLength={4000} onChange={e=>setNote(e.target.value)}/><button className="a-soft" data-action="publishing.review" disabled={locked||!note.trim()}>Record review</button></form>
   <p>Deploy release <code>{release.id}</code> to <strong>{siteId}</strong> with <strong>{data?.target?.accessPolicy||'loopback-only'}</strong> access.</p><button type="button" className="a-primary" disabled={locked||!release.review||selected?.status==='unknown'} data-action="publishing.deploy" onClick={()=>submit('publishing.deploy',{siteId,releaseId:release.id,expectedRevision:selected?.revision??0})}>Deploy reviewed release</button>
  </div>}
  {selected&&<div className="a-selection-card" aria-label="Site status"><h4>{selected.id}</h4><dl><dt>Status</dt><dd>{selected.status}</dd><dt>Revision</dt><dd>{selected.revision}</dd><dt>Access policy</dt><dd>{selected.accessPolicy}</dd><dt>Deployed release</dt><dd>{selected.releaseId||'None'}</dd><dt>Previous release</dt><dd>{selected.previousReleaseId||'None'}</dd></dl>{safeLink(selected.url)&&<p><a href={safeLink(selected.url)} target="_blank" rel="noopener noreferrer">Open deployed site</a><br/><code>{selected.url}</code></p>}
   <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="publishing.status" onClick={()=>inspect('publishing.status')}>Check site status</button><button type="button" className="a-soft" disabled={busy} data-action="publishing.logs" onClick={()=>inspect('publishing.logs')}>Read site logs</button></div>
   {selected.previousReleaseId&&<><p>Rollback restores release <code>{selected.previousReleaseId}</code>.</p><button type="button" className="a-soft" disabled={locked} data-action="publishing.rollback" onClick={()=>submit('publishing.rollback',{siteId,releaseId:selected.previousReleaseId,expectedRevision:selected.revision})}>Roll back to previous release</button></>}
   <p>Stopping or removing this site preserves its saved releases and receipts.</p><div className="a-dialog-actions"><button type="button" className="a-soft" disabled={locked||['stopped','removed'].includes(selected.status)} data-action="publishing.stop" onClick={()=>submit('publishing.stop',{siteId,expectedRevision:selected.revision})}>Stop site</button><button type="button" className="a-soft a-danger" disabled={locked||selected.status==='removed'} data-action="publishing.remove" onClick={()=>submit('publishing.remove',{siteId,expectedRevision:selected.revision})}>Remove site</button></div>
  </div>}
  {inspection&&<div aria-label="Publishing inspection"><h4>{inspection.action==='publishing.logs'?'Site logs':'Current site status'}</h4><pre className="a-state-view">{JSON.stringify(inspection.value,null,2)}</pre></div>}
  <details><summary>Durable publishing receipts ({data?.receipts?.length||0})</summary><div aria-label="Publishing receipts">{data?.receipts?.map(receipt=><div className="a-selection-card" key={receipt.id}><strong>{receipt.action} · {receipt.state}</strong><p>{receipt.siteId} · {receipt.createdAt}</p><p>Request: <code>{receipt.requestId}</code></p>{receipt.releaseId&&<p>Release: <code>{receipt.releaseId}</code></p>}{receipt.error&&<p>{description(receipt.error)}</p>}</div>)}</div></details>
 </section>;
}
