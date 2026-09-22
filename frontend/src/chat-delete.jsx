import React,{useEffect,useRef,useState} from 'react';

// Preview and delete share the same authoritative actions with agents. Never
// persist the review token in a browser draft or reuse it for a different chat.
export function ChatDelete({id,act,cancel}){
 const [preview,setPreview]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[cleanup,setCleanup]=useState(false);
 const generation=useRef(0),pending=useRef(false);
 async function review(){
  const version=++generation.current;setPreview(null);setError('');setBusy(true);
  try{const receipt=await act('session.deletePreview',{id});
   if(!receipt?.accepted||!receipt.result?.confirmationToken)throw Error(receipt?.error||'Could not review this chat for deletion.');
   if(version===generation.current)setPreview(receipt.result);
  }catch(e){if(version===generation.current)setError(e.message)}
  finally{if(version===generation.current)setBusy(false)}
 }
 useEffect(()=>{review();return()=>{generation.current++}},[id]);
 async function remove(){
  if(!preview||pending.current)return;
  const version=generation.current;pending.current=true;setBusy(true);setError('');
  try{const receipt=await act('session.delete',{id,confirmationToken:preview.confirmationToken});
   if(!receipt?.accepted)throw Error(receipt?.error||'Deletion was not confirmed. Review the chat again before retrying.');
   if(version===generation.current){if(receipt.result?.cleanupPending){setCleanup(true);setPreview(null)}else cancel();}
  }catch(e){if(version===generation.current){setError(e.message);setPreview(null)}}
  finally{pending.current=false;if(version===generation.current)setBusy(false)}
 }
 const summary=preview?.summary;
 return <div className="a-chat-delete" aria-busy={busy}>
  {preview?<><p>Permanently delete <strong>{preview.title||'this chat'}</strong>?</p><p>{preview.description}</p>
   {summary&&<p className="a-caption">{summary.conversationCount} saved conversation{summary.conversationCount===1?'':'s'} · {summary.fileCount} file{summary.fileCount===1?'':'s'} · {summary.attachmentCount} attachment{summary.attachmentCount===1?'':'s'} · {summary.artifactCount} artifact{summary.artifactCount===1?'':'s'} · {summary.shareCount} published link{summary.shareCount===1?'':'s'}</p>}
   <p><strong>This cannot be undone.</strong></p>
   {!!preview.preserved?.length&&<p className="a-caption">Kept: {preview.preserved.join('; ')}.</p>}
  </>:busy?<p role="status">Checking what will be deleted…</p>:null}
  {cleanup&&<p role="status">The chat has been removed. File cleanup is pending and will be retried automatically.</p>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
  <div className="a-dialog-actions">
   {preview?<button type="button" className="a-soft a-danger" data-action="session.delete" disabled={busy} onClick={remove}>{busy?'Deleting…':'Delete permanently'}</button>:!busy&&!cleanup&&<button type="button" className="a-soft" data-action="session.deletePreview" onClick={review}>Review again</button>}
   <button type="button" className="a-soft" data-action="view.update" disabled={pending.current} onClick={cancel}>{cleanup?'Close':'Keep chat'}</button>
  </div>
 </div>;
}
