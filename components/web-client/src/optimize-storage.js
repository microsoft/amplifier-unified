export const optimizePrefix='maintenance.optimize.';
export const optimizeRunning=status=>['previewing','applying','cancelling'].includes(status);
export const validKeepHours=value=>value===null||Number.isSafeInteger(value)&&value>0;
export const sameOptimizePolicy=(a,b)=>!!a&&!!b&&a.requestKeepHours===b.requestKeepHours&&a.responseKeepHours===b.responseKeepHours;
export const expirySelected=policy=>policy?.requestKeepHours!=null||policy?.responseKeepHours!=null;

export function optimizeScopeArgs(scope,sessionId){
 if(!['current','workspace','all'].includes(scope))throw Error('Choose a storage scope.');
 if(scope!=='all'&&!sessionId)throw Error('Select a conversation before using this scope.');
 return {scope,...(scope==='all'?{}:{sessionId})};
}

export function optimizeCutoff(value){
 if(value===null)return 'Not selected — diagnostics retained';
 const date=typeof value==='string'?new Date(value):null;
 return date&&!Number.isNaN(date.valueOf())?date.toISOString():'Not reported — do not apply expiry';
}