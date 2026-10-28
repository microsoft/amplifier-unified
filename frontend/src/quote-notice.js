// Quote feedback belongs to the chat that requested it. A late result, including a refusal after
// navigation, keeps its originating chat and is shown only there, never beneath another chat's composer.
export const quoteNotice=(sessionId,text)=>({sessionId,text});
export const visibleQuoteNotice=(notice,selectedSessionId)=>notice&&notice.sessionId!=null&&notice.sessionId===selectedSessionId?notice.text:'';
// Sending from a chat takes its quotes with it, so that chat's quote feedback ends there.
export const quoteNoticeAfterSend=(notice,sentSessionId)=>notice&&notice.sessionId===sentSessionId?null:notice;
