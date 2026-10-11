import React,{useEffect,useRef,useState} from 'react';
const bytes=value=>value<1024?`${value} B`:value<1048576?`${(value/1024).toFixed(1)} KB`:value<1073741824?`${(value/1048576).toFixed(1)} MB`:`${(value/1073741824).toFixed(2)} GB`;
export function WorkspaceCleanup({id,act,onDone}){
 const [review,setReview]=useState(null),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null);
 async function run(confirm=false){setBusy(true);setError('');try{
  const response=await act(confirm?'workspace.deleteFiles':'workspace.deleteFilesPreview',confirm?{id,confirmationToken:review.token,confirmationPath:path}:{id});
  if(!response?.accepted||!response.result)throw Error(response?.error||'Workspace cleanup could not complete. Check the reported error and review again.');
  if(confirm){setResult(response.result);onDone?.()}else{setReview(response.result);setPath('')}
 }catch(e){setError(e.message||'The folder could not be checked.')}finally{setBusy(false)}}
 return <section aria-label="Workspace file cleanup">
  {!result&&<button type="button" disabled={busy} onClick={()=>run()}>Review workspace file cleanup</button>}
  {error&&<p role="alert">{error}</p>}
  {review&&!result&&<div><p>{review.description}</p><p><strong>{review.managed?'App-managed workspace':'User-selected project folder'}</strong> · {bytes(review.inventory.fileBytes)} in {review.inventory.fileCount} files. {review.chatCount} chats will be retained and archived.</p><code style={{overflowWrap:'anywhere'}}>{review.path}</code><label className="a-field">Type the exact folder path to confirm permanent deletion<input aria-label="Confirm workspace folder path" value={path} onChange={e=>setPath(e.target.value)} disabled={busy}/></label><button type="button" className="a-danger" disabled={busy||path!==review.path} onClick={()=>run(true)}>Permanently delete workspace files</button></div>}
  {result&&<p role="status">{result.status==='completed'?`Workspace files deleted (${bytes(result.deletedBytes)} of file content). Chat history was retained.`:result.message||'Cleanup was interrupted; inspect the recorded folder before proceeding.'}{result.status!=='completed'&&<code style={{display:'block',overflowWrap:'anywhere'}}>{result.cleanupPath}</code>}</p>}
 </section>;
}
export function StorageSettings({act}){
 const [args,setArgs]=useState({}),[data,setData]=useState(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[filter,setFilter]=useState(''),[page,setPage]=useState(0);
 const key=JSON.stringify(args),action=useRef(act);action.current=act;
 useEffect(()=>{let live=true;setData(null);setError('');action.current('storage.inspect',args).then(response=>{if(!response?.result)throw Error(response?.error||'Storage could not be read.');if(live)setData(response.result)}).catch(e=>{if(live)setError(e.message||'Storage could not be read.')});return()=>{live=false}},[key,refresh]);
 function go(next){setArgs(next);setPage(0);setFilter('')}
 const filtered=(data?.rows||[]).filter(row=>row.label.toLowerCase().includes(filter.toLowerCase())),rows=filtered.slice(page*50,(page+1)*50);
 return <section aria-label="Storage usage"><p>Disk usage on the Amplifier host. Shared CLI history and configuration are preserved. Archiving hides work; it does not free disk space.</p><div className="a-dialog-actions">{args.scope&&<button type="button" onClick={()=>go(data?.parent?{...args,path:data.parent}:{})}>Back</button>}<button type="button" onClick={()=>setRefresh(x=>x+1)}>Refresh storage</button></div>{error&&<p role="alert">{error}</p>}{!data&&!error?<p role="status">Measuring storage…</p>:data&&<>
 <p><strong>{data.incomplete?'At least ':''}{bytes(data.bytes)}</strong>{data.path&&<code style={{display:'block',overflowWrap:'anywhere'}}>{data.path}</code>}</p>{data.description&&<p>{data.description}</p>}{data.incomplete&&<p>Some files were unreadable, linked, or beyond the scan limit. These counts are incomplete.</p>}
 <label className="a-field">Filter folders and files<input aria-label="Filter storage" value={filter} onChange={e=>{setFilter(e.target.value);setPage(0)}}/></label>
 <ul style={{listStyle:'none',padding:0}}>{rows.map(row=><li key={row.id} style={{padding:'.7rem 0',borderBottom:'1px solid var(--a-border)'}}><button type="button" disabled={!!args.scope&&!row.directory} onClick={()=>go(args.scope?{...args,path:row.path}:{scope:row.scope,...(row.workspaceId?{workspaceId:row.workspaceId}:{})})}>{row.label}</button> <span>{row.includedIn?`Included in ${row.includedIn}`:`${row.incomplete?'≥ ':''}${bytes(row.bytes)}`}</span>{!row.includedIn&&<progress style={{display:'block',width:'100%'}} max={Math.max(1,data.bytes)} value={row.bytes} aria-label={`${row.label}: ${bytes(row.bytes)}`}/>}</li>)}</ul>
 {filtered.length>50&&<div><button disabled={!page} onClick={()=>setPage(p=>p-1)}>Previous</button> Page {page+1} of {Math.ceil(filtered.length/50)} <button disabled={(page+1)*50>=filtered.length} onClick={()=>setPage(p=>p+1)}>Next</button></div>}
 {args.scope==='workspace'&&(!args.path||args.path==='.')&&<WorkspaceCleanup key={args.workspaceId} id={args.workspaceId} act={act} onDone={()=>{}}/>}
 </>}</section>;
}
