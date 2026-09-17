/** Re-measure after any draft update, including agent-origin updates. */
export function resizeComposer(element){
 if(!element)return;
 element.style.minHeight='44px';
 element.style.maxHeight='180px';
 element.style.height='auto';
 const height=Math.max(44,Math.min(180,element.scrollHeight));
 element.style.height=`${height}px`;
 element.style.overflowY=element.scrollHeight>180?'auto':'hidden';
 element.style.resize='none';
}
