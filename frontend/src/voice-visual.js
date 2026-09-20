/** No recording loop: a selected source stays local until an explicit request. */
export class VoiceVisualClient {
 constructor({request,onState=()=>{},getVoice,media=globalThis.navigator?.mediaDevices}) {
  Object.assign(this,{request,onState,getVoice,media});this.generation=0;this.stream=null;this.grant=null;this.video=null;
  this.onPageHide=()=>this.stop();globalThis.window?.addEventListener('pagehide',this.onPageHide);
 }
 state(patch){this.value={...this.value,...patch};this.onState(this.value)}
 active(callId){const voice=this.getVoice();return voice?.status==='connected'&&voice.id===callId}
 async choose() {
  const voice=this.getVoice();
  if(voice?.status!=='connected'||!voice.id)throw Error('Connect a voice call before choosing a screen source.');
  if(!this.media?.getDisplayMedia)throw Error('Screen capture is unavailable in this browser. Use a desktop browser on localhost or HTTPS.');
  this.stop();const generation=this.generation;this.state({status:'choosing',error:null});
  try {
   // Must run synchronously from a user click; an agent cannot open this picker.
   const stream=await this.media.getDisplayMedia({video:{width:{ideal:1280},height:{ideal:1280}},audio:false});
   if(generation!==this.generation||!this.active(voice.id)){stream.getTracks().forEach(t=>t.stop());throw Error('The call changed while choosing a source.');}
   this.stream=stream;const track=stream.getVideoTracks()[0];
   if(!track)throw Error('The selected source has no video track.');
   track.addEventListener('ended',()=>this.stop());
   const video=document.createElement('video');video.muted=true;video.playsInline=true;video.srcObject=stream;this.video=video;
   await video.play();
   if(generation!==this.generation||!this.active(voice.id))throw Error('The call ended while preparing the source.');
   const kind=track.getSettings().displaySurface||'unknown',source={kind:['browser','window','monitor'].includes(kind)?kind:'unknown',label:(track.label||'User-selected source').slice(0,200)};
   const grant=await this.request('/api/voice/visual/grant',{method:'POST',body:{sessionId:voice.sessionId,callId:voice.id,source}});
   if(generation!==this.generation||!this.active(voice.id)){await this.request('/api/voice/visual/revoke',{method:'POST',body:{grantId:grant.id}}).catch(()=>{});throw Error('The call changed; source permission was discarded.');}
   this.grant=grant;this.expiry=setTimeout(()=>this.stop(),Math.max(0,grant.expiresAt*1000-Date.now()));this.state({status:'ready',source,grantId:grant.id});
  } catch(error){this.stop();this.state({status:'error',error:error.name==='NotAllowedError'?'Screen permission was not granted.':error.message});throw error}
 }
 sync(voice,connected,serverVisual) {
  if(this.stream&&(!connected||!this.active(this.grant?.callId||voice?.id)||this.grant&&(serverVisual?.available===false||serverVisual?.id&&serverVisual.id!==this.grant.id)))this.stop();
 }
 async capture(command) {
  const grant=this.grant,generation=this.generation;
  const target={requestId:command.id,grantId:command.grantId,sessionId:command.sessionId,callId:command.callId};
  try {
   if(!grant||command.grantId!==grant.id||command.callId!==grant.callId||command.sessionId!==grant.sessionId||!this.active(grant.callId))throw Error('This screen source is no longer authorized.');
   const track=this.stream?.getVideoTracks()[0];if(!track||track.readyState!=='live'||track.muted)throw Error('The selected source is unavailable or paused.');
   const video=this.video;
   await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('No fresh screen frame arrived.')),2000);if(!video.requestVideoFrameCallback){clearTimeout(timer);reject(Error('Fresh frame capture is unsupported by this browser.'));return}video.requestVideoFrameCallback(()=>{clearTimeout(timer);resolve()})});
   if(generation!==this.generation||!this.active(grant.callId)||track.readyState!=='live'||track.muted)throw Error('Screen sharing ended before capture.');
   if(!video.videoWidth||!video.videoHeight)throw Error('The source did not provide visible pixels.');
   const canvas=document.createElement('canvas');const scale=Math.min(1,1280/Math.max(video.videoWidth,video.videoHeight));canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
   const image=canvas.toDataURL('image/png').split(',')[1];if(image.length>666668)throw Error('This image is too large. Select a smaller window.');
   const capturedAt=Date.now()/1000;
   await this.request('/api/voice/visual/complete',{method:'POST',body:{...target,image,capturedAt}});
   this.state({status:'ready',capturedAt,error:null});
  } catch(error){this.state({error:error.message});await this.request('/api/voice/visual/complete',{method:'POST',body:{...target,error:error.message}}).catch(()=>{});}
 }
 stop(){
  ++this.generation;clearTimeout(this.expiry);const grant=this.grant;this.grant=null;
  this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;
  if(this.video){this.video.pause();this.video.srcObject=null;this.video=null}
  this.state({status:'idle',source:null,grantId:null});
  if(grant)this.request('/api/voice/visual/revoke',{method:'POST',body:{grantId:grant.id},keepalive:true}).catch(()=>{});
 }
 dispose(){this.stop();globalThis.window?.removeEventListener('pagehide',this.onPageHide)}
}
