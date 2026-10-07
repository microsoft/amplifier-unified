export function usageMetric(metric, currency=false){
 if(!metric||metric.value==null)return '';
 const value=metric.value.toLocaleString(undefined,{maximumFractionDigits:currency?6:0});
 const notes=[];
 if(metric.estimatedCalls>0)notes.push('includes estimates');
 return `${currency?'$':''}${value}${notes.length?' · '+notes.join(' · '):''}`;
}

export function usageExport(snapshot){
 const {sessionId,scope,source,calls,metrics,providers,coverage,excludedUnboundCalls}=snapshot.usage;
 return {observedAt:snapshot.observedAt,sessionId,scope,source,calls,metrics,providers,coverage,excludedUnboundCalls};
}
