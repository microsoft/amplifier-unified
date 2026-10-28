// Quote feedback belongs to the chat that requested it. A late result, including a refusal after
// navigation, keeps its originating chat and is shown only there, never beneath another chat's composer.
export const quoteNotice=(sessionId,text)=>({sessionId,text});
export const visibleQuoteNotice=(notice,selectedSessionId)=>notice&&notice.sessionId!=null&&notice.sessionId===selectedSessionId?notice.text:'';
