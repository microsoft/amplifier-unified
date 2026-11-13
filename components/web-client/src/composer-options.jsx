import React from 'react';
import {ModelControl,useNextMessageConfiguration} from './chat-controls';
import {BundleControl} from './bundle-controls';

/** One quiet model/effort entry below the field at every conversation width. */
export function ComposerOptions({state,session,act,working,detail}){
 // One dialog owns one acknowledged revision chain across its model and bundle.
 // External client revisions still cannot replace a touched form's captured one.
 const nextMessageControl=useNextMessageConfiguration(session,act);
 if(session?.externalAgent)return <span className="a-caption" data-external-agent>{session.externalAgent.label||session.externalAgent.id}</span>;
 if(!session&&state?.view?.newSessionDraft?.externalAgentId)return <span className="a-caption" data-external-agent>Model and tools are managed by the external agent.</span>;
 const props={state,session,act,working,nextMessageControl};
 return <ModelControl {...props} compact detail={detail}>
  <BundleControl {...props} compact embedded/>
 </ModelControl>;
}
