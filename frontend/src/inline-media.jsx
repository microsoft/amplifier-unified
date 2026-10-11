import React,{useEffect,useRef,useState} from 'react';
import * as api from './api';
import {GifPlayer} from './gif-player';
import './inline-media.css';

let observationGeneration=0;
function mediaUrl(item,messageId,download=false){
 const query=new URLSearchParams({sessionId:item.sessionId,messageId,version:String(item.version),sha256:item.sha256});
 if(download)query.set('download','1');
 return api.clientUrl(`/api/outputs/${encodeURIComponent(item.id)}/media?${query}`);
}

function MediaFigure({item,messageId}){
 const canvas=useRef(null),video=useRef(null),player=useRef(null),reporter=useRef(()=>{});
 const [status,setStatus]=useState({render:'loading',playback:'paused',detail:''});
 useEffect(()=>{
  let disposed=false,sequence=0;const generation=++observationGeneration;
  const abort=new AbortController(),url=mediaUrl(item,messageId);
  const report=(render,playback,detail='')=>{
   if(disposed)return;setStatus({render,playback,detail});
   void api.request('/api/actions',{method:'POST',body:{action:'outputs.mediaReport',args:{
    sessionId:item.sessionId,messageId,id:item.id,version:item.version,sha256:item.sha256,
    generation,sequence:++sequence,renderState:render,playback,detail:detail.slice(0,500)
   }}}).catch(()=>{}); // report failure never reattaches/regenerates media
  };
  reporter.current=report;report('loading','paused');
  const pause=()=>{player.current?.pause();video.current?.pause()};
  const motion=matchMedia('(prefers-reduced-motion: reduce)');
  const onMotion=()=>{if(motion.matches)pause()},onVisibility=()=>{if(document.hidden)pause()};
  document.addEventListener('visibilitychange',onVisibility);motion.addEventListener('change',onMotion);
  if(item.media.mimeType==='image/gif'){
   if(!globalThis.ImageDecoder)report('error','paused','GIF playback is unsupported here. Download the original.');
   else void (async()=>{
    let decoder;
    try{
     const response=await fetch(url,{credentials:'same-origin',signal:abort.signal});
     if(!response.ok)throw new Error(`Media is unavailable (${response.status}).`);
     const data=await response.arrayBuffer();if(disposed)return;
     decoder=new ImageDecoder({data,type:'image/gif',preferAnimation:true});
     const owned=new GifPlayer({decoder,loopCount:item.media.loopCount,report,
      draw:frame=>{const target=canvas.current;if(!target||disposed)return;
       target.width=frame.displayWidth;target.height=frame.displayHeight;
       const context=target.getContext('2d');context.clearRect(0,0,target.width,target.height);context.drawImage(frame,0,0);
      }});
     player.current=owned;await owned.ready();
     if(disposed)owned.dispose();
    }catch(error){decoder?.close();if(!disposed&&error.name!=='AbortError')report('error','paused',error.message)}
   })();
  }
  return ()=>{
   pause();disposed=true;abort.abort();player.current?.dispose();player.current=null;reporter.current=()=>{};
   if(video.current){video.current.removeAttribute('src');video.current.load()}
   document.removeEventListener('visibilitychange',onVisibility);motion.removeEventListener('change',onMotion);
  };
 },[item.id,item.version,item.sha256,messageId]);
 return <figure data-media-id={item.id} data-media-state={status.render} data-playback={status.playback}>
  {item.media.mimeType==='image/gif'?<canvas ref={canvas} aria-label={item.title}/>:<video
   ref={video} src={mediaUrl(item,messageId)} controls playsInline muted preload="metadata" aria-label={item.title}
   onLoadedData={()=>reporter.current('ready','paused')}
   onPlay={()=>reporter.current('ready','playing')} onPause={()=>reporter.current('ready','paused')}
   onEnded={()=>reporter.current('ready','ended')} onError={()=>reporter.current('error','paused','Video cannot be decoded. Download the original.')}/>}
  <figcaption>{item.title}</figcaption>
  <div className="media-controls">
   {item.media.mimeType==='image/gif'&&status.render==='ready'&&<button type="button" className="a-soft"
    onClick={()=>status.playback==='playing'?player.current?.pause():player.current?.play()}>
    {status.playback==='playing'?'Pause GIF':'Play GIF'}</button>}
   <a href={mediaUrl(item,messageId,true)} download={item.filename}>Download original</a>
   <span role="status">{status.detail||`${status.render} · ${status.playback}`}</span>
  </div>
 </figure>;
}

export function InlineMedia({sessionId,messageId,mediaRevision=0}){
 const anchor=useRef(null),[visible,setVisible]=useState(false),[result,setResult]=useState(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(rows=>setVisible(rows.some(row=>row.isIntersecting)),{rootMargin:'200px'});
  if(anchor.current)observer.observe(anchor.current);return ()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  if(!visible)return;let disposed=false;const abort=new AbortController();
  const query=new URLSearchParams({sessionId,messageId});
  setError('');
  api.request(`/api/outputs/media?${query}`,{signal:abort.signal}).then(value=>{if(!disposed)setResult(value)}).catch(error=>{if(!disposed&&error.name!=='AbortError')setError(error.message)});
  return ()=>{disposed=true;abort.abort()};
 },[sessionId,messageId,mediaRevision,visible,retry]);
 return <div ref={anchor} className="a-inline-media">
  {visible&&result?.items.map(item=><MediaFigure key={`${item.id}:${item.version}:${item.sha256}`} item={item} messageId={messageId}/>)}
  {error&&<p role="alert">{error} <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry media metadata</button></p>}
  {result?.overflow&&<p>Only the first 20 media attachments are displayed.</p>}
 </div>;
}