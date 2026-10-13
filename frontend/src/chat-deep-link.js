// Opening a native notification is an explicit navigation in this browser only.
// Never accept commands, credentials, or an external redirect from the link.
export function chatDeepLink(search) {
 const id=new URLSearchParams(search).get('chat');
 return id&&id.length<=200&&!/[\x00-\x1f\x7f]/.test(id)?id:null;
}
