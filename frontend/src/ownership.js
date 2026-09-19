/** Ownership is reported by the host; opening a view never requests takeover. */
export function ownershipState(session) {
  const state=session?.ownership, source=state?.source||session?.lockOwner?.app||'another application';
  switch(state?.status){
    case 'blocked': return {blocked:true,canTakeover:true,label:`In use by ${source}`,detail:'Continue here to request this session. Your draft is kept.'};
    case 'yielded': return {blocked:true,canTakeover:true,label:`Continued in ${source}`,detail:'This view stays connected. Choose Continue here when you want to resume.'};
    case 'yielding': return {blocked:true,canTakeover:false,label:`Moving to ${source}`,detail:'Finishing current work and saving the session…'};
    case 'taking-over': return {blocked:true,canTakeover:false,label:'Taking over…',detail:'Waiting for the current owner to finish, then loading your session.'};
    case 'yield-failed': return {blocked:true,canTakeover:false,label:'The session could not be released',detail:state.detail||'Saving or cleanup needs attention. Ownership is retained.'};
    default:return {blocked:false,canTakeover:false};
  }
}
