import React,{useEffect,useRef,useState} from 'react';
import {Archive,ArchiveRestore,ArrowUp,ArrowDown,Plus,Share2,Copy} from 'lucide-react';

export function LibraryFilters({state,act}){
 const view=state.view||{},organization=state.conversationOrganization||{},collections=organization.collections||[];
 return <div className="a-form-grid" aria-label="Conversation library filters">
  <label>Conversations<select aria-label="Active or archived conversations" value={view.navArchive||'active'} data-action="view.update" onChange={e=>act('view.update',{patch:{navArchive:e.target.value}})}><option value="active">Active</option><option value="archived">Archived ({organization.archivedCount||0})</option><option value="all">Active and archived</option></select></label>
  <label>Collection<select aria-label="Filter by collection" value={view.navCollection||''} data-action="view.update" onChange={e=>act('view.update',{patch:{navCollection:e.target.value||null}})}><option value="">All collections</option>{view.navCollection&&!collections.some(row=>row.id===view.navCollection)&&<option value={view.navCollection}>Removed collection</option>}{collections.map(row=><option key={row.id} value={row.id}>{row.name} ({row.count})</option>)}</select></label>
 </div>;
}

export function ConversationLibrary({state,session,act}){
 const organization=state.conversationOrganization||{},collections=organization.collections||[];
 const collection=collections.find(row=>row.sessionIds?.includes(session.id)),archived=Object.hasOwn(organization.archived||{},session.id);
 const nameVersion=useRef(0);
 const [name,setName]=useState(''),[rename,setRename]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>setRename(collection?.name||''),[collection?.id,collection?.name]);
 async function run(action,args){
  setBusy(true);setError('');
  try{const result=await act(action,args);if(!result?.accepted)throw Error('The change was not saved.');return result}
  catch(e){setError(e.message)}finally{setBusy(false)}
 }
 const reorder=(index,offset)=>{const ids=collections.map(row=>row.id);[ids[index],ids[index+offset]]=[ids[index+offset],ids[index]];run('collection.reorder',{ids})};
 return <section className="a-settings-section"><h3>Conversation library</h3>
  <p>Archive keeps this conversation and its files. Work already running continues.</p>
  <button type="button" className="a-soft" disabled={busy} data-action={archived?'session.restore':'session.archive'} onClick={()=>run(archived?'session.restore':'session.archive',{id:session.id})}>{archived?<ArchiveRestore/>:<Archive/>}{archived?'Restore conversation':'Archive conversation'}</button>
  <label htmlFor="conversation-collection">Collection</label><select id="conversation-collection" value={collection?.id||''} disabled={busy} data-action="collection.assign" onChange={e=>run('collection.assign',{sessionId:session.id,id:e.target.value||null})}><option value="">No collection</option>{collections.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select>
  <form onSubmit={async e=>{e.preventDefault();const version=nameVersion.current;if(await run('collection.create',{name})){if(nameVersion.current===version)setName('')}}}><label htmlFor="new-chat-collection">New collection name</label><input id="new-chat-collection" value={name} maxLength={100} onChange={e=>{nameVersion.current++;setName(e.target.value)}}/><button type="submit" className="a-soft" data-action="collection.create" disabled={busy||!name.trim()}><Plus/>Create collection</button></form>
  {collection&&<><label htmlFor="rename-chat-collection">Collection name</label><input id="rename-chat-collection" maxLength={100} value={rename} onChange={e=>setRename(e.target.value)}/><div className="a-dialog-actions"><button type="button" className="a-soft" data-action="collection.rename" disabled={busy||!rename.trim()} onClick={()=>run('collection.rename',{id:collection.id,name:rename})}>Save collection name</button><button type="button" className="a-soft" data-action="collection.remove" disabled={busy} onClick={()=>run('collection.remove',{id:collection.id})}>Remove collection; keep chats</button></div></>}
  {collections.length>1&&<ul className="a-catalog-list" aria-label="Collection order">{collections.map((row,index)=><li key={row.id}><span>{row.name}</span><button type="button" className="a-icon" aria-label={'Move '+row.name+' up'} data-action="collection.reorder" disabled={busy||index===0} onClick={()=>reorder(index,-1)}><ArrowUp/></button><button type="button" className="a-icon" aria-label={'Move '+row.name+' down'} data-action="collection.reorder" disabled={busy||index===collections.length-1} onClick={()=>reorder(index,1)}><ArrowDown/></button></li>)}</ul>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </section>;
}

export function ConversationSharing({session,act}){
 const [preview,setPreview]=useState(null),[links,setLinks]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[expiry,setExpiry]=useState(604800);
 const generation=useRef(0),[nextOffset,setNextOffset]=useState(null);
 useEffect(()=>{generation.current++;setPreview(null);setLinks([]);setNextOffset(null);setError('');setBusy(false);return ()=>{generation.current++}},[session.id]);
 async function run(action,args,apply){
  const current=generation.current;setBusy(true);setError('');
  try{const receipt=await act(action,args);if(!receipt?.result)throw Error('The snapshot action did not return a result.');if(current===generation.current)apply(receipt.result)}
  catch(e){if(current===generation.current)setError(e.message)}finally{if(current===generation.current)setBusy(false)}
 }
 const upsert=row=>setLinks(current=>[row,...current.filter(item=>item.id!==row.id)]);
 const list=(offset=0)=>run('session.shareList',{sessionId:session.id,offset},result=>{setLinks(current=>offset?[...current,...result.items.filter(row=>!current.some(item=>item.id===row.id))]:result.items);setNextOffset(result.nextOffset)});
 return <section className="a-settings-section"><h3>Share a snapshot</h3>
  <p>A snapshot includes visible conversation text and voice exchanges. File contents and interactive artifacts are not embedded.</p>
  <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="session.sharePreview" onClick={()=>run('session.sharePreview',{sessionId:session.id},setPreview)}><Share2/>Preview snapshot</button><button type="button" className="a-soft" disabled={busy} data-action="session.shareList" onClick={()=>list()}>Show shared snapshots</button></div>
  {preview&&<><pre className="a-state-view" aria-label="Snapshot preview" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:320}}>{preview.text}</pre>{preview.nextOffset!==null&&<button type="button" className="a-link" disabled={busy} data-action="session.shareRead" onClick={()=>run('session.shareRead',{id:preview.id,offset:preview.nextOffset},page=>setPreview(current=>({...current,text:current.text+page.text,nextOffset:page.nextOffset})))}>Read more of this snapshot</button>}
   <p>Anyone with the link can read this snapshot while this host is reachable. Later edits stay private to the original chat. You can revoke the link.</p>
   <label htmlFor="share-expiry">Link expires after</label><select id="share-expiry" value={expiry} onChange={e=>setExpiry(Number(e.target.value))}><option value={86400}>One day</option><option value={604800}>One week</option><option value={2592000}>Thirty days</option></select>
   <button type="button" className="a-primary" disabled={busy} data-action="session.shareCreate" onClick={()=>run('session.shareCreate',{id:preview.id,contentHash:preview.contentHash,visibility:'anyone_with_link',expiresInSeconds:expiry},result=>{upsert(result);setPreview(null)})}>Create share link</button>
  </>}
  {links.map(row=><div className="a-selection-card" key={row.id}><strong>{row.title}</strong><p>{row.status}{row.expiresAt?' · expires '+new Date(row.expiresAt*1000).toLocaleString():''}</p>{row.path&&<><label htmlFor={'share-'+row.id}>Snapshot link</label><input id={'share-'+row.id} readOnly value={new URL(row.path,window.location.origin).href}/><button type="button" className="a-soft" onClick={()=>navigator.clipboard.writeText(new URL(row.path,window.location.origin).href).catch(()=>setError('Copy was unavailable. Select the link and copy it.'))}><Copy/>Copy link</button><button type="button" className="a-soft" disabled={busy} data-action="session.shareRevoke" onClick={()=>run('session.shareRevoke',{id:row.id},upsert)}>Revoke link</button></>}</div>)}
  {nextOffset!==null&&<button type="button" className="a-soft" disabled={busy} data-action="session.shareList" onClick={()=>list(nextOffset)}>Load older snapshots</button>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </section>;
}
