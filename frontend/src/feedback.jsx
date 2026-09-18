import React,{useEffect,useRef,useState} from 'react';
import {ExternalLink,Send,Plus,Paperclip,File,X,Image as ImageIcon} from 'lucide-react';
import {ResultNotice} from './settings-ui';
import './feedback.css';

const empty=()=>({title:'',body:'',category:'bug',includeDiagnostics:false,attachments:[]});
const sizeLabel=bytes=>bytes<1024?`${bytes} B`:bytes<1024*1024?`${Math.ceil(bytes/1024)} KB`:`${(bytes/(1024*1024)).toFixed(1)} MB`;
const encodeFile=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Could not read this file.'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file)});
export function FeedbackPanel({state,act}){
 const shared=state.view?.feedbackDraft;
 const [draft,setDraft]=useState(()=>shared||empty()),[busy,setBusy]=useState(false),[error,setError]=useState(''),[uploading,setUploading]=useState(false),[dragging,setDragging]=useState(false),[retryUpload,setRetryUpload]=useState(null);
 const current=useRef(draft),submitting=useRef(false),fileInput=useRef(null),staging=useRef(false);
 useEffect(()=>{if(shared){current.current=shared;setDraft(shared)}},[shared]);
 const requests=state.feedback?.requests||[],pending=draft.pending;
 const result=pending&&requests.find(item=>item.requestId===pending.requestId);
 const shown=pending||draft;
 useEffect(()=>{if(result&&['submitted','failed','unknown'].includes(result.status))setError('')},[result?.status]);
 const working=busy||['queued','sending'].includes(result?.status),done=['submitted','failed','unknown'].includes(result?.status);
 const frozen=!!pending||uploading,preview=draft.previewId,selected=(draft.attachments||[]).filter(row=>!pending||pending.attachmentIds?.includes(row.id));
 const issues=state.feedback?.issuesUrl||'https://github.com/bkrabach/amplifier-unified/issues';
 function save(next){current.current=next;setDraft(next);return act('view.update',{patch:{feedbackDraft:next}})}
 function edit(patch){setError('');save({...current.current,...patch}).catch(()=>setError('The draft could not be saved. Reconnect and try again.'))}
 function setPreview(id){edit({previewId:id})}
 function acceptDraft(response){const next=response?.state?.view?.feedbackDraft;if(next){current.current=next;setDraft(next)}}
 async function stageFiles(files,retry){
  if(staging.current||current.current.pending)return;
  staging.current=true;setUploading(true);setError('');setDragging(false);
  try{
   if(retry){acceptDraft(await act('feedback.attachment.add',retry));setRetryUpload(null)}
   for(const file of files){
    if(!file.size||file.size>8*1024*1024)throw new Error('Choose nonempty files up to 8 MB each.');
    const request={requestId:crypto.randomUUID(),name:file.name||'pasted-image.png',base64:await encodeFile(file)};
    try{acceptDraft(await act('feedback.attachment.add',request))}
    catch(error){setRetryUpload(request);throw error}
   }
  }catch(error){setError(error?.message||'The file could not be added. Check the draft and retry; it will not be added twice.')}
  finally{staging.current=false;setUploading(false)}
 }
 async function removeFile(id){if(frozen)return;setError('');try{acceptDraft(await act('feedback.attachment.remove',{id}));if(preview===id)setPreview(null)}catch(error){setError(error?.message||'The attachment could not be removed.')}}
 function paste(event){const files=Array.from(event.clipboardData?.files||[]);if(files.length){event.preventDefault();event.stopPropagation();if(!frozen)stageFiles(files)}}
 function drop(event){if(!event.dataTransfer?.types?.includes('Files'))return;event.preventDefault();event.stopPropagation();setDragging(false);if(!frozen)stageFiles(Array.from(event.dataTransfer.files||[]))}
 async function submit(event){
  event.preventDefault();if(submitting.current||staging.current||working||done||retryUpload)return;
  submitting.current=true;setBusy(true);setError('');
  // Freeze both text and identity before the first await. A lost response must
  // never generate a second GitHub issue or submit newly edited text as a retry.
  const source=current.current,payload=source.pending||{requestId:crypto.randomUUID(),title:source.title,body:source.body,category:source.category,includeDiagnostics:!!source.includeDiagnostics,attachmentIds:(source.attachments||[]).map(row=>row.id)};
  try{await save({...source,pending:payload});await act('feedback.submit',payload)}
  catch{setError('The submission was not acknowledged. Check its status using the same request below; this will not post it twice.')}
  finally{submitting.current=false;setBusy(false)}
 }
 async function startNew(){if(working)return;setError('');setRetryUpload(null);try{await save(empty())}catch{setError('Could not start a new draft. Reconnect and try again.')}}
 const facts=state.feedback?.diagnostics||{};
 return <section className={`a-feedback${dragging?' is-dragging':''}`} data-part="feedback" onPaste={paste} onDrop={drop} onDragOver={event=>{if(event.dataTransfer?.types?.includes('Files')){event.preventDefault();event.stopPropagation();if(!frozen)setDragging(true)}}} onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget))setDragging(false)}}>
  <p>Send a bug report, idea, or question to <a href={issues} target="_blank" rel="noopener noreferrer">bkrabach/amplifier-unified</a>. Uses the host’s GitHub sign-in.</p>
  {(result||error)&&<div className="a-feedback-result"><ResultNotice phase={result?.status==='submitted'?'success':['failed','unknown'].includes(result?.status)?'error':'working'} message={result?.message}/>{error&&<ResultNotice phase="error" message={error}/>} {result?.url&&<a className="a-feedback-link" href={result.url} target="_blank" rel="noopener noreferrer">View issue <ExternalLink size={14}/></a>} {result?.status==='unknown'&&<><a href={issues} target="_blank" rel="noopener noreferrer">Check repository issues <ExternalLink size={14}/></a>{result.attachmentsUrl&&<a href={result.attachmentsUrl} target="_blank" rel="noopener noreferrer">Check attachment branch <ExternalLink size={14}/></a>}</>}</div>}
  <form onSubmit={submit} data-action="feedback.submit">
   <label htmlFor="feedback-category">Type</label><select id="feedback-category" data-action="view.update" value={shown.category||'bug'} disabled={frozen} onChange={e=>edit({category:e.target.value})}><option value="bug">Bug report</option><option value="idea">Feature idea</option><option value="question">Question</option><option value="other">Other feedback</option></select>
   <label htmlFor="feedback-title">Title</label><input id="feedback-title" data-action="view.update" maxLength={200} required disabled={frozen} value={shown.title||''} onChange={e=>edit({title:e.target.value})} placeholder="A short description"/>
   <label htmlFor="feedback-body">Details</label><textarea id="feedback-body" data-action="view.update" maxLength={16000} rows={4} required disabled={frozen} value={shown.body||''} onChange={e=>edit({body:e.target.value})} placeholder="What happened, what you expected, or what you’d like to do…"/>
   <div className="a-feedback-files" aria-label="Feedback attachments">
    <input ref={fileInput} type="file" multiple hidden disabled={frozen} aria-label="Choose feedback files" onChange={event=>{const files=Array.from(event.target.files||[]);event.target.value='';stageFiles(files)}}/>
    {!pending&&<button type="button" className="a-soft a-feedback-add-files" data-action="feedback.attachment.add" disabled={uploading||!!retryUpload} onClick={()=>fileInput.current?.click()}><Paperclip size={16}/>{uploading?'Adding files…':'Add files or images'}</button>}
    {!pending&&<p className="a-caption">Or drop files here, or paste an image. Up to 8 files, 8 MB each, 24 MB total.</p>}
    {!!selected.length&&<ul>{selected.map(row=><li key={row.id}>
     <button type="button" className="a-feedback-file-preview" data-action="view.update" disabled={uploading} aria-label={`Preview ${row.name}`} onClick={()=>setPreview(preview===row.id?null:row.id)}>{row.mime?.startsWith('image/')&&/^[a-f0-9]{32}$/.test(row.id)?<img src={`/api/attachments/${row.id}`} alt=""/>:<File size={22}/>}<span><strong>{row.name}</strong><small>{sizeLabel(row.size)} · {pending?'Included':'Ready to include'}</small></span></button>
     {!pending&&<button type="button" className="a-icon" data-action="feedback.attachment.remove" aria-label={`Remove ${row.name}`} disabled={uploading} onClick={()=>removeFile(row.id)}><X size={16}/></button>}
    </li>)}</ul>}
    {selected.filter(row=>row.id===preview&&/^[a-f0-9]{32}$/.test(row.id)).map(row=><div className="a-feedback-preview" key={row.id}>{row.mime?.startsWith('image/')?<img src={`/api/attachments/${row.id}`} alt={`Preview of ${row.name}`}/>:<File size={32}/>}<a href={`/api/attachments/${row.id}`} download={row.name}>{row.mime?.startsWith('image/')?<ImageIcon size={14}/>:<File size={14}/>}Open {row.name}</a></div>)}
    {retryUpload&&<div className="a-feedback-upload-retry"><button type="button" className="a-soft" disabled={uploading} onClick={()=>stageFiles([],retryUpload)}>Check attachment: {retryUpload.name}</button><button type="button" className="a-soft" disabled={uploading} onClick={()=>{setRetryUpload(null);setError('')}}>Stop retrying</button></div>}
   </div>
   <label className="a-feedback-checkbox"><input type="checkbox" data-action="view.update" checked={!!shown.includeDiagnostics} disabled={frozen} onChange={e=>edit({includeDiagnostics:e.target.checked})}/>Include app version and operating system</label>
   {shown.includeDiagnostics&&<p className="a-caption">Included: app {facts.appVersion||'version not reported'} · {facts.osFamily||'OS not reported'}</p>}
   <p className="a-caption">Only your text and the files listed above are sent when you submit. Files stay in this private repository’s history and are linked from the issue; your GitHub sign-in needs repository Contents write access. Optional diagnostics add the two facts above. Chats, paths, provider settings, and credentials are not attached automatically.</p>
   <div className="a-dialog-actions">
    {!done&&<button type="submit" className="a-primary" data-action="feedback.submit" disabled={working||uploading||!!retryUpload||!shown.title?.trim()||!shown.body?.trim()}><Send/>{working?'Sending…':pending?'Check submission':'Send feedback'}</button>}
    {done&&<button type="button" className="a-soft" data-action="view.update" onClick={startNew}><Plus/>New feedback</button>}
   </div>
  </form>
  {requests.filter(item=>item.url&&item.requestId!==pending?.requestId).length>0&&<div className="a-feedback-recent"><h3>Recently sent</h3>{requests.filter(item=>item.url&&item.requestId!==pending?.requestId).slice(0,5).map(item=><a key={item.requestId} href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<ExternalLink size={14}/></a>)}</div>}
 </section>;
}
