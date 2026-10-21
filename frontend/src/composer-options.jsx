import React from 'react';
import {ModelControl} from './chat-controls';
import {BundleControl} from './bundle-controls';

/** One quiet model/effort entry below the field at every conversation width. */
export function ComposerOptions({state,session,act,working}){
 const props={state,session,act,working};
 return <ModelControl {...props} compact>
  <BundleControl {...props} compact embedded/>
 </ModelControl>;
}
