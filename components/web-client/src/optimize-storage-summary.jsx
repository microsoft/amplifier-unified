import React from 'react';
import {formatStorageBytes} from './storage-bytes';

const measured=value=>Number.isSafeInteger(value)&&value>=0?formatStorageBytes(value):'Not measured';
const count=value=>Number.isSafeInteger(value)&&value>=0?value:'Not reported';
const byteLabels={
 source:'Original file content',
 inlineRemoved:'Inline image content removed',
 assetsAdded:'Unique image assets added',
 refsAndJournalAdded:'References and journal content added',
 recoveryRetained:'Recovery content retained',
 unlinked:'File content unlinked'
};

export function OptimizeStorageSummary({operation}){
 const coverage=operation.summary?.coverage||{},bytes=operation.summary?.bytes||{};
 const delta=bytes.filesystemDelta;
 return <div aria-label="Saved storage operation results">
  <p role="status">Status: {operation.status}. {operation.message}</p>
  <p className="a-caption">Operation <code>{operation.operationId}</code> · revision {operation.revision}</p>
  <h5>Coverage</h5>
  <p>{coverage.complete===true?'Coverage complete for the selected scope.':'Coverage is incomplete or not confirmed. Unchecked files are not claimed optimized.'}</p>
  <dl>{[['roots','Root sessions'],['workers','Worker sessions'],['held','Held'],['unsupported','Unsupported'],['malformed','Malformed'],['drift','Changed since preview']].map(([key,label])=><React.Fragment key={key}><dt>{label}</dt><dd>{count(coverage[key])}</dd></React.Fragment>)}</dl>
  <h5>{['previewing','previewed'].includes(operation.status)?'Preview accounting — not reclaimed space':'Recorded accounting'}</h5>
  <dl>{Object.entries(byteLabels).map(([key,label])=><React.Fragment key={key}><dt>{label}</dt><dd>{measured(bytes[key])}</dd></React.Fragment>)}
   <dt>Observed filesystem change</dt><dd>{Number.isSafeInteger(delta)?`${delta>0?'+':delta<0?'−':''}${formatStorageBytes(Math.abs(delta))}`:'Not measured'}</dd>
  </dl>
  <p className="a-caption">Content sizes are logical bytes, not free disk space. A positive filesystem change means more storage used; a negative change means less. Retained recovery copies can offset reductions. Missing measurements are not zero. Image extraction and diagnostic expiry must not be counted twice.</p>
 </div>;
}