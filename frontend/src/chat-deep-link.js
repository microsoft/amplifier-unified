// Opening a native notification is an explicit navigation in this browser only.
// Never accept commands, credentials, or an external redirect from the link.
export function chatDeepLink(search) {
 const id=new URLSearchParams(search).get('chat');
 return id&&id.length<=200&&!/[\x00-\x1f\x7f]/.test(id)?id:null;
}

// Launch events can arrive before boot, and again while the same PWA is open.
// Serialize selections and keep the receiver alive across React effect cleanup.
export function createChatLinkReceiver({location,history,launchQueue,reportError}) {
 let select=null,pending=[],running=false,firstLaunch=true;
 async function drain(){
  if(running||!select)return;
  running=true;
  try{while(select&&pending.length){
   const url=pending.shift(),id=chatDeepLink(url.search);
   try{
    const result=await select(id);
    if(result.accepted&&chatDeepLink(location.search)===id){
     const clean=new URL(location.href);clean.searchParams.delete('chat');history.replaceState(history.state,'',clean);
    }
   }catch(error){reportError(error)}
  }}finally{running=false}
 }
 function receive(target){
  let url;try{url=new URL(target,location.href)}catch{return}
  if(url.origin!==location.origin||url.pathname!==location.pathname||!chatDeepLink(url.search))return;
  // Cold launch can arrive through both location and launchQueue.
  if(pending.some(item=>item.href===url.href))return;
  pending.push(url);void drain();
 }
 receive(location.href);
 const initialURL=location.href;
 launchQueue?.setConsumer?.(params=>{
  const duplicate=firstLaunch&&params.targetURL===initialURL;firstLaunch=false;
  if(params.targetURL&&!duplicate)receive(params.targetURL);
 });
 return {bind(callback){select=callback;void drain();return()=>{select=null}},receive};
}
