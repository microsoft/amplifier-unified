import React,{useEffect,useRef,useState} from 'react';

export function RecallSettings({session,act}){
 const [query,setQuery]=useState(''),[scope,setScope]=useState('workspace'),[coverage,setCoverage]=useState(null),[results,setResults]=useState(null),[source,setSource]=useState(null),[notes,setNotes]=useState([]),[nextNotes,setNextNotes]=useState(null),[text,setText]=useState(''),[memoryScope,setMemoryScope]=useState('task'),[memoryView,setMemoryView]=useState('available'),[editing,setEditing]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const alive=useRef(true),inFlight=useRef(false),sid=session?.id,activeSid=useRef(sid);
 activeSid.current=sid;
 useEffect(()=>{setResults(null);setSource(null);setNotes([]);setNextNotes(null);setEditing(null);setText('');setCoverage(null);setError('')},[sid]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 async function run(action,args,apply){
  if(inFlight.current)return;
  inFlight.current=true;setBusy(true);setError('');
  try{const response=await act(action,{sessionId:sid,...args});if(!response?.accepted)throw Error('The request was not accepted.');if(alive.current&&activeSid.current===sid)apply?.(response.result);return response.result}
  catch(e){if(alive.current)setError(e.message)}finally{inFlight.current=false;if(alive.current)setBusy(false)}
 }
 useEffect(()=>{
  if(!sid||coverage?.status!=='indexing')return;
  const timer=setInterval(()=>run('recall.status',{},setCoverage),750);
  return()=>clearInterval(timer);
 },[sid,coverage]);
 const search=(offset=0)=>run('recall.search',{query,scope,offset},value=>{setResults(old=>offset?{...value,items:[...old.items,...value.items]}:value);setCoverage(value.coverage)});
 const list=(offset=0,scope=memoryView)=>run('memory.list',{scope,offset},value=>{setNotes(old=>offset?[...old,...value.items]:value.items);setNextNotes(value.nextOffset)});
 const read=row=>run('recall.read',{sourceSessionId:row.sessionId,messageId:row.messageId,sourceRevision:row.sourceRevision},setSource);
 const save=async event=>{event.preventDefault();const value=await run(editing?'memory.update':'memory.create',{...(editing?{id:editing.id,expectedRevision:editing.revision,allScopes:memoryView==='all'}:{scope:memoryScope}),text});if(value){setText('');setEditing(null);await list()}};
 if(!sid)return <p>Choose a conversation to set the task and workspace scope.</p>;
 return <>
  <section className="a-settings-section" aria-label="Indexed recall"><h3>Find past work</h3><p>Search saved conversation text. Reading a result leaves your current conversation and draft in place.</p>
   <button type="button" className="a-soft" disabled={busy||coverage?.status==='indexing'} data-action="recall.refresh" onClick={()=>run('recall.refresh',{},setCoverage)}>Update search index</button>
   {coverage&&<p role="status">{coverage.status} · {coverage.indexed} of {coverage.total??'unknown'} conversations indexed{coverage.errorCount?` · ${coverage.errorCount} unavailable`:''}. {coverage.checkedAt?'Last checked '+new Date(coverage.checkedAt*1000).toLocaleString():''}</p>}
   <form onSubmit={e=>{e.preventDefault();search()}}><label htmlFor="recall-query">Search conversation text</label><input id="recall-query" value={query} maxLength={500} onChange={e=>setQuery(e.target.value)}/><label htmlFor="recall-scope">Search scope</label><select id="recall-scope" value={scope} onChange={e=>setScope(e.target.value)}><option value="task">This conversation</option><option value="workspace">This workspace</option><option value="all">All registered conversations</option></select><button type="submit" className="a-primary" data-action="recall.search" disabled={busy||!query.trim()}>Search past work</button></form>
   {results&&<><p>Matches contain all search terms. Coverage and source dates matter; an empty partial search does not prove nothing exists.</p>{results.items.map(row=><div className="a-selection-card" key={row.sessionId+row.messageId}><strong>{row.session.title}</strong><p>{row.snippet}</p><small>{row.role}{row.via==='call'?' · voice':''}</small><button type="button" className="a-soft" disabled={busy} data-action="recall.read" onClick={()=>read(row)}>Read source</button></div>)}{results.nextOffset!==null&&<button type="button" className="a-soft" disabled={busy} onClick={()=>search(results.nextOffset)}>More matches</button>}</>}
   {source&&<div className="a-selection-card" aria-label="Verified source"><strong>Source verified for this read</strong><pre className="a-state-view" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{source.text}</pre><small>Conversation {source.sessionId} · message {source.messageId}</small>{source.nextOffset!==null&&<button type="button" className="a-soft" disabled={busy} data-action="recall.read" onClick={()=>run('recall.read',{sourceSessionId:source.sessionId,messageId:source.messageId,sourceRevision:source.sourceRevision,offset:source.nextOffset},value=>setSource(old=>({...value,text:old.text+value.text})))}>Read more</button>}</div>}
  </section>
  <section className="a-settings-section" aria-label="Saved memory"><h3>Saved memory</h3><p>Notes are reference material. They do not grant permission or start work. Nothing is saved here automatically.</p>
   <label htmlFor="memory-view">Inspect scope</label><select id="memory-view" disabled={busy} value={memoryView} onChange={e=>{setMemoryView(e.target.value);list(0,e.target.value)}}><option value="available">Notes for this work</option><option value="all">All saved notes, including removed chats</option></select>
   <button type="button" className="a-soft" disabled={busy} data-action="memory.list" onClick={()=>list()}>Inspect saved memory</button>
   <form onSubmit={save}><label htmlFor="memory-text">Memory note</label><textarea id="memory-text" disabled={busy} value={text} maxLength={8000} rows={4} onChange={e=>setText(e.target.value)}/>{!editing&&<><label htmlFor="memory-scope">Remember for</label><select id="memory-scope" disabled={busy} value={memoryScope} onChange={e=>setMemoryScope(e.target.value)}><option value="task">This conversation</option><option value="workspace">This workspace</option><option value="global">All work</option></select></>}<button type="submit" className="a-primary" data-action={editing?'memory.update':'memory.create'} disabled={busy||!text.trim()}>{editing?'Save correction':'Save memory'}</button>{editing&&<button type="button" className="a-soft" onClick={()=>{setEditing(null);setText('')}}>Cancel edit</button>}</form>
   {notes.map(note=><div key={note.id} className="a-selection-card"><strong>{note.scope} · revision {note.revision}</strong><p style={{whiteSpace:'pre-wrap'}}>{note.text}</p><small>Saved through {note.provenance.origin}</small><div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="memory.read" onClick={()=>run('memory.read',{id:note.id,allScopes:memoryView==='all'},value=>{setEditing(value);setText(value.text)})}>Inspect or correct</button><button type="button" className="a-soft" disabled={busy} data-action="memory.delete" onClick={async()=>{if(await run('memory.delete',{id:note.id,expectedRevision:note.revision,allScopes:memoryView==='all'})){if(editing?.id===note.id){setEditing(null);setText('')}await list()}}}>Delete memory</button></div>{note.source&&<button type="button" className="a-link" disabled={busy} onClick={()=>read(note.source)}>Read original source</button>}</div>)}
   {nextNotes!==null&&<button type="button" className="a-soft" disabled={busy} onClick={()=>list(nextNotes)}>More saved notes</button>}
   {editing&&<details><summary>Retained revisions ({editing.versions.length}, up to 50)</summary>{editing.versions.map(version=><p key={version.revision}>Revision {version.revision}: {version.text}</p>)}</details>}
   <p>Deleting removes the saved note and its retained revisions. Original chats and existing private backups remain.</p>
  </section>
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </>;
}
