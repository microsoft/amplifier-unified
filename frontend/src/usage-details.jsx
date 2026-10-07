import React from 'react';
import {usageMetric} from './conversation-usage.js';
import {cachedPercent,usageMetrics,usageRows} from './usage-details.js';

export function UsageDetails({usage,metrics=usageMetrics(usage),label='Token usage',costLabel='Cost (USD)'}){
 const cached=cachedPercent(metrics);
 return <section className="a-usage-details" aria-label={label}>
  <dl className="a-usage-grid">{usageRows(metrics,costLabel).map(({key,label,metric})=><div key={key}><dt>{label}</dt><dd>{usageMetric(metric,key==='costUsd')}{key==='grossInputTokens'&&cached!=null&&<small> · {cached} cached</small>}</dd></div>)}</dl>
  <p className="a-caption">Input includes cache reads and writes. Total = input + output. Reasoning is included in output.{metrics.costUsd?.value!=null&&!(metrics.costUsd.estimatedCalls>0)&&' Cost reported by the provider.'}</p>
 </section>;
}
