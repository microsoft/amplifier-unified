import React,{useState} from 'react';
import {useShellContext} from './shell/runtime';
import {usageMetric} from './conversation-usage.js';
import {cachedPercent,usageMetrics,usageRows} from './usage-details.js';

export function UsageDetails({usage,metrics=usageMetrics(usage),label='Token usage',costLabel='Cost (USD)'}){
 const shell=useShellContext(),presentation=shell?.composition?.presentation;
 const level=presentation?.interfaceDetail||presentation?.executionDetail||'standard';
 const [expanded,setExpanded]=useState(null),open=expanded??level==='detailed';
 const cached=cachedPercent(metrics),rows=usageRows(metrics,costLabel);
 const primary=new Set(['grossInputTokens','outputTokens','grossTotalTokens','costUsd']);
 const primaryRows=rows.filter(row=>primary.has(row.key));
 const accountingRows=rows.filter(row=>!primary.has(row.key)||row.key==='costUsd').map(row=>row.key==='costUsd'?{...row,label:'Exact cost (USD)'}:row);
 if(!rows.length)return null;
 const grid=(items,compact=false)=><dl className="a-usage-grid">{items.map(({key,label,metric})=>{
  const percent=key==='grossInputTokens'?cached:['cacheReadTokens','cacheWriteTokens'].includes(key)?cachedPercent(metrics,key):null;
  return <div key={key}><dt>{label}</dt><dd>{usageMetric(metric,key==='costUsd',compact)}{percent!=null&&<small>{percent} {key==='grossInputTokens'?'cached':'of input'}</small>}</dd></div>;
 })}</dl>;
 return <section className="a-usage-details" aria-label={label}>
  {primaryRows.length>0&&grid(primaryRows,true)}
  {accountingRows.length>0&&<details className="a-usage-accounting" open={open} onToggle={event=>{if(event.currentTarget.open!==open)setExpanded(event.currentTarget.open)}}>
   <summary>Cache and accounting details</summary>
   {grid(accountingRows)}
   <p className="a-caption">Input includes cache reads and writes. Total = input + output. Reasoning is included in output.{metrics.costUsd?.value!=null&&!(metrics.costUsd.estimatedCalls>0)&&' Cost reported by the provider.'}</p>
  </details>}
 </section>;
}
