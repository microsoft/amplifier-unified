import React,{useEffect,useRef,useState} from 'react';

export function ProviderMessageTest({state,id,sessionId,act,disabled=false}){
 const [pending,setPending]=useState(false),[error,setError]=useState('');
 const sending=useRef(false),request=useRef(null);
 const op=state.setup?.operations?.['providers.testMessage:'+id];
 const result=state.setup?.messageTests?.[id];
 useEffect(()=>{if(request.current&&op?.commandId===request.current&&['ready','error'].includes(op.phase)){
  sending.current=false;setPending(false);setError(op.error||'');request.current=null;
 }},[op?.commandId,op?.phase]);
 async function send(){
  if(sending.current)return;
  sending.current=true;setPending(true);setError('');
  try{
   const receipt=await act('providers.testMessage',{id,...(sessionId?{sessionId}:{})});
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'The test could not be started.');
   request.current=receipt.operationId;
   // The operation may have completed before its acceptance reached the UI.
   setPending(receipt.operationId);
  }catch(e){sending.current=false;setPending(false);setError(e.message);}
 }
 useEffect(()=>{if(request.current&&op?.commandId===request.current&&['ready','error'].includes(op.phase)){
  sending.current=false;setPending(false);setError(op.error||'');request.current=null;
 }},[pending]);
 return <div data-part="provider-message-test">
  <button type="button" className="a-soft" data-action="providers.testMessage" disabled={!id||disabled||!!pending} onClick={send}>{pending?'Sending test message…':'Send test message'}</button>
  <p className="a-caption">Sends a small message using the saved model and credentials. Provider charges may apply. No chat is created.</p>
  {error&&<p role="alert">{error}</p>}
  {!pending&&result&&<div role="status"><p><strong>{result.reachable?'Test message succeeded':'Test message failed'}</strong> · {result.model} · {(result.elapsedMs/1000).toFixed(1)} s{result.statusCode?` · HTTP ${result.statusCode}`:''}</p><p>{result.reachable?result.response:result.error}</p></div>}
 </div>;
}
