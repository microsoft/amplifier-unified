import React from 'react';
import {LoaderCircle} from 'lucide-react';

export function WorkProjectionStatus({session}){
 const progress=session?.workProjection;
 const binding=session?.workBindingCoverage;
 if(progress?.status==='ready'&&binding?.unassociated>0)return <p className="a-session-history" data-part="work-binding-status">
  {binding.status==='partial-source'?'Earlier or incomplete work has not been assigned to a turn.':'Some saved work has no verified turn placement.'}
  {' '}It remains available under Recent saved work.
 </p>;
 if(!progress||progress.status==='ready')return null;
 const retrying=progress.status==='retrying',unavailable=progress.status==='unavailable';
 if(!retrying&&!unavailable&&progress.status!=='loading')return null;
 return <p role="status" className="a-session-history" data-part="work-projection-status">
  {!unavailable&&<LoaderCircle className="a-progress-spinner" aria-hidden="true"/>}
  {unavailable?'Work details exceed the current loading limit. Your conversation is still available.':retrying?'Work details could not finish loading. Retrying shortly. You can keep reading this chat.':'Loading work details…'}
 </p>;
}
