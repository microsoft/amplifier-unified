// Local intent is not shared acceptance. One flight per emoji preserves click
// order; different messages/emoji never wait for each other or a model turn.
export function createReactionController({sessionId,messageId,clientId,send,read,notify=()=>{},save=()=>{},restored=[]}){
 let confirmed={revision:-1,reactions:[]},error='',checking=false;
 const pending=new Map(restored.map(row=>[row.emoji,{...row,status:'unknown',restored:true}]));
 const changed=()=>{save([...pending.values()]);notify()};
 function observe(value){
  if(Number.isSafeInteger(value?.annotationRevision)&&value.annotationRevision>=confirmed.revision)
   confirmed={revision:value.annotationRevision,reactions:[...(value.reactions||[])]};
  else if(confirmed.revision<0)confirmed={revision:-1,reactions:[...(value?.reactions||[])]};
 }
 function view(){const selected=new Set(confirmed.reactions);for(const [emoji,row] of pending)(row.desired?selected.add:selected.delete).call(selected,emoji);return {reactions:[...selected],pending:[...pending.keys()],unknown:[...pending.values()].some(row=>row.status==='unknown'),error,checking}}
 function settle(row,receipt){
  const value=receipt?.result;
  if(receipt?.accepted!==true||value?.sessionId!==sessionId||value.messageId!==messageId||value.reactionActor!=='user'||!Array.isArray(value.reactions)||!Number.isSafeInteger(value.annotationRevision))throw Object.assign(Error('Reaction save could not be confirmed.'),{unconfirmed:true});
  observe(value);
  if(pending.get(row.emoji)!==row)return;
  pending.delete(row.emoji);
  if(row.desired!==row.present){
   if(row.restored)error='Your later reaction change was not sent. Choose it again.';
   else start(row.emoji,row.desired);
  }
  changed();
 }
 async function flight(row){
  try{settle(row,await send(row))}
  catch(e){
   if(pending.get(row.emoji)!==row)return;
   if(e.unconfirmed||!e.status||e.status>=500){row.status='unknown';error='Reaction not confirmed. Check its saved state before trying again.'}
   else {pending.delete(row.emoji);error=e.message||'The reaction was not saved.';if(row.desired!==row.present&&row.desired!==confirmed.reactions.includes(row.emoji))start(row.emoji,row.desired)}
   changed();
  }
 }
 function start(emoji,present){const row={emoji,present,desired:present,id:crypto.randomUUID(),clientId,status:'sending'};pending.set(emoji,row);changed();void flight(row)}
 function set(emoji,present){error='';const row=pending.get(emoji);if(row){row.desired=present;changed()}else start(emoji,present)}
 async function reconcile(){
  if(checking)return;checking=true;changed();
  try{
   for(const row of [...pending.values()].filter(row=>row.status==='unknown')){
    const value=await read(row);
    if(value?.sessionId!==sessionId||value.messageId!==messageId)throw Error('Reaction state belongs to another message.');
    if(value.status==='accepted'){error='';settle(row,value.receipt);observe({...value.current,annotationRevision:value.revision});}
    else error='Reaction outcome is still unknown. No write was repeated.';
   }
  }catch(e){error=e.message||'Reaction state could not be checked.'}
  finally{checking=false;changed()}
 }
 return {observe,view,set,reconcile};
}
