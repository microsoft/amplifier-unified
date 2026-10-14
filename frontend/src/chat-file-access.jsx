import React,{useEffect,useRef,useState} from 'react';
import {PathField,ResultNotice} from './settings-ui';

function Paths({label,values}){
 return <div><strong>{label}</strong>{values?.length?<ul>{values.map(value=><li className="a-wrap" key={value}>{value}</li>)}</ul>:<p>None added.</p>}</div>;
}

export function ChatFileAccess({state,session,act}){
 const [report,setReport]=useState(null),[allowed,setAllowed]=useState(''),[denied,setDenied]=useState('');
 const [pending,setPending]=useState(null),[sending,setSending]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const request=useRef(false),alive=useRef(true);
 const run=async(action)=>{
  if(request.current)return;
  request.current=true;setSending(true);setError('');setNotice('');
  try{
   const args={sessionId:session.id,scope:'session',...(action==='permissions.get'?{}:{expectedRevision:report.revision}),
    ...(action==='permissions.save'?{allowed:allowed.split('\n').map(v=>v.trim()).filter(Boolean),denied:denied.split('\n').map(v=>v.trim()).filter(Boolean)}:{})};
   const receipt=await act(action,args);
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'Could not update file settings. Your entries are kept.');
   if(alive.current)setPending({id:receipt.operationId,action});
  }catch(e){if(alive.current){request.current=false;setError(e.message)}}
  finally{if(alive.current)setSending(false)}
 };
 useEffect(()=>{alive.current=true;run('permissions.get');return()=>{alive.current=false}},[session.id]);
 const completion=pending&&state.managementResults?.[pending.id];
 useEffect(()=>{
  if(!pending||!completion)return;
  request.current=false;setPending(null);
  if(completion.phase==='error'){setError(completion.error||'Could not update file settings. Reload the preview before retrying.');return}
  const value=state.permissions;
  if(value?.scope!=='session'||value.sessionId!==session.id||value.commandId!==pending.id){setError('The file-settings view changed. Reload the preview.');return}
  setReport(value);setAllowed(value.allowed.join('\n'));setDenied(value.denied.join('\n'));setNotice(value.detail||'');
 },[pending,completion,state.permissions,session.id]);
 const busy=sending||!!pending;
 const ready=report&&report.sessionId===session.id;
 return <section className="a-settings-section">
  <p>These write settings apply only to this chat. Reading files, shared project settings, and other chats stay unchanged.</p>
  <p className="a-caption">File tools always include the workspace unless denied or restricted by the bundle. Denied folders take precedence. Tools and child workers may have stricter limits; shell commands have separate policies.</p>
  <button type="button" className="a-soft" data-action="permissions.get" disabled={busy} onClick={()=>run('permissions.get')}>Reload preview</button>
  {ready&&<>
   <p>{report.overridden.length?'This chat has its own write overrides.':'This chat inherits workspace write defaults.'}</p>
   <fieldset className="a-permissions-fields" disabled={busy}>
    <label htmlFor="chat-write-allowed">Allowed folders (one per line)</label>
    <PathField id="chat-write-allowed" multiline directory state={state} act={act} value={allowed} onChange={setAllowed}/>
    <label htmlFor="chat-write-denied">Denied folders (one per line)</label>
    <PathField id="chat-write-denied" multiline directory state={state} act={act} value={denied} onChange={setDenied}/>
   </fieldset>
   <button type="button" className="a-primary" data-action="permissions.save" disabled={busy} onClick={()=>run('permissions.save')}>Save for this chat</button>
   <section aria-label="Workspace defaults preview">
    <h3>Workspace defaults after reset</h3>
    <Paths label="Allowed folders" values={report.inherited.allowed}/>
    <Paths label="Denied folders" values={report.inherited.denied}/>
    <p>Returning to defaults removes this chat’s write overrides. This can allow more files, or fewer. Other tool and bundle restrictions still apply.</p>
    <button type="button" className="a-soft" data-action="permissions.reset" disabled={busy||!report.overridden.length} onClick={()=>run('permissions.reset')}>Return to workspace defaults</button>
   </section>
  </>}
  <p className="a-caption">Saved changes take effect after active work finishes, before this chat’s next message. They do not start a new turn.</p>
  {session.fileAccessPending&&<p role="status">Write settings are pending. Existing work keeps its current access until it finishes. Finish or stop existing child work before sending another message. No new turn has started.</p>}
  {busy&&<ResultNotice phase="working" message="Updating file settings…"/>}
  {error&&<ResultNotice phase="error" message={error}/>}
  {notice&&<ResultNotice phase="ready" message={notice}/>}
 </section>;
}
