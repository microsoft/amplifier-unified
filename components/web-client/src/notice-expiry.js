// A routine confirmation's reading interval counts only while available to read.
// Explicit Keep open in the renderer disables expiry for non-pointer readers.
export const NOTICE_EXPIRY_MS=15_000;
export function createNoticeExpiry(expire,{duration=NOTICE_EXPIRY_MS,now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout}={}){
 let remaining=duration,started=now(),handle=null,disposed=false;
 const pauses=new Set();
 const stop=()=>{if(handle!==null){cancel(handle);handle=null;remaining=Math.max(0,remaining-(now()-started))}};
 const start=()=>{if(disposed||pauses.size||handle!==null)return;started=now();handle=schedule(()=>{handle=null;if(!disposed){disposed=true;expire()}},remaining)};
 start();
 return {
  pause(reason,paused){if(disposed)return;if(paused){stop();pauses.add(reason)}else{pauses.delete(reason);start()}},
  dispose(){stop();disposed=true},
 };
}