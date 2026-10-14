import React from 'react';
import {LoaderCircle} from 'lucide-react';

export function WorkProjectionStatus({session}){
 const progress=session?.workProjection;
 if(!progress||progress.status==='ready')return null;
 const retrying=progress.status==='retrying';
 if(!retrying&&progress.status!=='loading')return null;
 return <p role="status" className="a-session-history" data-part="work-projection-status">
  <LoaderCircle className="a-progress-spinner" aria-hidden="true"/>
  {retrying?'Work details could not finish loading. Retrying shortly. You can keep reading this chat.':'Loading work details…'}
 </p>;
}
