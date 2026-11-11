export function notificationBody(message,settings={}){
 const fallback=message.kind==='needs-input'?'Amplifier needs your input.':'Your Amplifier response is ready.';
 if(message.kind==='needs-input')return fallback; // Response-preview consent does not expose decisions.
 return settings.preview===true?(message.text?.slice(0,180)||fallback):fallback;
}

// Existing completion notifications keep their preference. New decision alerts
// are separately opt-in, and never expose an approve/answer operation.
export function notificationEvents(state){
 const events=notificationMessages(state).map(message=>({...message,kind:'response',id:JSON.stringify(['response',message.sessionId,message.id])}));
 for(const item of state.notificationAttention??state.attention?.items??[]){
  if(!item.sessionId||!item.fingerprint||!/^question:|^approval:/.test(item.id||''))continue;
  events.push({id:JSON.stringify(['needs-input',item.sessionId,item.id,item.fingerprint]),sessionId:item.sessionId,kind:'needs-input',read:item.read});
 }
 return events;
}
export function takeNotifications(state,seen,{connected,permission,hidden}){
 const fresh=[];
 for(const event of notificationEvents(state)){
  if(seen.has(event.id))continue;
  seen.add(event.id); // Disabled, visible or disconnected events are not replayed.
  if(!connected||permission!=='granted'||!desktopNotificationsEnabled(state.notificationSettings))continue;
  if(event.kind==='needs-input'&&(state.notificationSettings?.needsInput!==true||event.read))continue;
  if(hidden||event.sessionId!==state.selectedSessionId)fresh.push(event);
 }
 return fresh;
}
export async function showBrowserNotification(message,settings,{window=globalThis.window,navigator=globalThis.navigator,icon,onClick}={}){
 const options={body:notificationBody(message,settings),icon,tag:message.id,data:{sessionId:message.sessionId}};
 // Installed mobile browsers require ServiceWorkerRegistration.showNotification.
 // Do not wait for a new worker, subscribe to push, or request permission here.
 const registration=await navigator?.serviceWorker?.getRegistration?.();
 if(registration?.active&&registration.showNotification){
  await registration.showNotification('Amplifier',options);return;
 }
 const notice=new window.Notification('Amplifier',options);
 notice.onclick=onClick;
}
export function desktopNotificationsEnabled(settings={}){return settings.desktop!==false}

// Completed off-page conversations may no longer have message bodies in the
// browser snapshot. Their compact notification records survive that transition.
export function notificationMessages(state){
 if(Array.isArray(state.notificationMessages))return state.notificationMessages.filter(message=>message.id&&message.sessionId&&message.role==='assistant'&&message.via==='text');
 const messages=new Map();
 for(const session of state.sessions||[])for(const message of session.messages||[]){
  if(message.id&&message.role==='assistant'&&message.via==='text')messages.set(message.id,{...message,sessionId:session.id});
 }
 return [...messages.values()];
}
