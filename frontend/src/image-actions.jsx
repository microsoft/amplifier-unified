import React,{useState} from 'react';
import {Download,MessageSquare} from 'lucide-react';

export function ImageActions({canvas,dispatch}){
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const target=()=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,canvas[key]]));
 const run=async action=>{
  if(busy)return;setBusy(true);setNotice('');
  try{
   await dispatch(action==='draft'?'canvas.views.imageDraft':'canvas.views.command',action==='draft'?target():{...target(),action:'canvas.download',args:{}});
   if(action==='draft')setNotice('Image added to your draft. Add your instructions, then send.');
  }catch(error){setNotice(error.message)}finally{setBusy(false)}
 };
 return <div className="a-image-actions"><button type="button" className="a-soft" disabled={busy} onClick={()=>run('download')}><Download/>Download image</button><button type="button" className="a-soft" disabled={busy} onClick={()=>run('draft')}><MessageSquare/>{busy?'Working…':'Use in a follow-up'}</button>{notice&&<p role="status">{notice}</p>}</div>;
}
