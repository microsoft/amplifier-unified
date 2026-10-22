/** Presentation only: keep short prompts compact; give rich/long content full width. */
export function composerExpanded({draft='',attachments=[],quote=null}={}){
 return !!quote||attachments.length>0||draft.includes('\n')||draft.length>80;
}

/** Re-measure after any draft update, including agent-origin updates. */
export function resizeComposer(element){
 if(!element)return;
 element.style.minHeight='44px';
 element.style.maxHeight=element.querySelector?.('[data-quote-instance]')?'min(400px, 45dvh)':'180px';
 element.style.height='auto';
 // Rich text blocks size naturally; only textareas need scrollHeight sizing.
 if(element.hasAttribute?.('contenteditable')){element.style.overflowY='auto';return;}
 const height=Math.max(44,Math.min(180,element.scrollHeight));
 element.style.height=`${height}px`;
 element.style.overflowY=element.scrollHeight>180?'auto':'hidden';
 element.style.resize='none';
}

/** Content wins over voice/Stop; a pending upload must never become a voice launch. */
export function composerPrimaryAction({draft='',attachments=[],uploading=false,working=false,responseActive=false,stopping=false,voiceStatus='idle',voiceStarting=false,busy=false,newChatPending=false,sending=false,historyPending=false,executionUnavailable=false}={}){
 const content=!!draft.trim()||attachments.length>0;
 if(content||uploading)return {action:'conversation.send',label:'Send message',icon:'send',disabled:!content||busy||newChatPending||uploading||historyPending||executionUnavailable};
 if(!['idle','ended','error'].includes(voiceStatus))return {action:'call.end',label:'End voice call',icon:'cancel',disabled:voiceStatus==='ending'};
 if(responseActive)return {action:'conversation.stop',label:'Stop response',icon:'cancel',disabled:stopping||executionUnavailable};
 if(newChatPending||sending)return {action:'conversation.send',label:'Send message',icon:'send',disabled:true};
 return {action:'call.start',label:'Start voice call',icon:'voice',disabled:busy||voiceStarting||historyPending||executionUnavailable};
}
