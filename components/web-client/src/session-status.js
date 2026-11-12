/** Human-readable runtime status; only server-reported progress is displayed. */
import {ownershipState} from './ownership.js';
export function sessionStatus(session) {
  if (!session) return {label:'Call it. Text it. Chat with it.',detail:'',busy:false};
  const ownership=ownershipState(session);
  if(ownership.blocked)return {label:ownership.label,detail:ownership.detail,busy:false};
  const progress=session.progress;
  const agent=session.externalAgent&&(session.externalAgent.label||session.externalAgent.id);
  const detail=typeof progress==='string'?progress:progress?.message||progress?.detail||progress?.description||'';
  switch(session.status){
    case 'starting': return {
      label:agent?`Connecting to ${agent}…`:'Preparing your Amplifier session…',
      detail,
      explanation:agent?'Your message stays in this conversation while the agent connects.':'First launch loads your configured bundle and tools. Your message stays in this conversation while Amplifier gets ready.',
      busy:true,
    };
    case 'stopping': return {label:'Stopping the current work…',detail,busy:true};
    case 'working': case 'running': case 'busy': return {label:`${agent||'Amplifier'} is working…`,detail,busy:true};
    case 'error': case 'failed': return {label:`${agent||'Amplifier'} needs your attention`,detail:'',busy:false};
    case 'stopped': return {label:'Work stopped. Ready for your next message.',detail:'',busy:false};
    default:return {label:'Ready when you are',detail:'',busy:false};
  }
}
