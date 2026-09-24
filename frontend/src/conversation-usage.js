export function usageMetric(metric, currency=false){
 if(!metric||metric.value==null)return metric?.status==='empty'?'No recorded calls':metric?.status==='pending'?'Pending':'Unavailable';
 const value=metric.value.toLocaleString(undefined,{maximumFractionDigits:currency?6:0});
 const notes=[];
 if(metric.estimatedCalls>0)notes.push('includes estimates');
 if(metric.pendingCalls>0)notes.push(`${metric.pendingCalls} pending`);
 if(metric.unknownCalls>0)notes.push(`${metric.unknownCalls} unavailable`);
 return `${currency?'$':''}${value}${notes.length?' · '+notes.join(' · '):''}`;
}

export function usageExport(snapshot){
 const {sessionId,scope,source,calls,metrics,providers,coverage,excludedUnboundCalls}=snapshot.usage;
 return {observedAt:snapshot.observedAt,sessionId,scope,source,calls,metrics,providers,coverage,excludedUnboundCalls};
}
