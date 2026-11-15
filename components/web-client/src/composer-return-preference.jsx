import React,{useId,useState,useSyncExternalStore} from 'react';
import {automaticReturnMode,createReturnKeyPreference,returnKeyMode,returnKeyHelp} from './composer-return.js';

const preference=createReturnKeyPreference();
let automatic;
export function useComposerReturn(){
 const saved=useSyncExternalStore(preference.subscribe,preference.getSnapshot,preference.getSnapshot);
 const [capturedAutomatic]=useState(()=>automatic??=automaticReturnMode());
 return {...saved,mode:returnKeyMode(saved.value,capturedAutomatic),automatic:capturedAutomatic,set:preference.set};
}

export function ComposerReturnSettings(){
 const {value,error,mode,automatic,set}=useComposerReturn(),id=useId();
 return <section className="a-appearance-settings a-appearance-preference-group" aria-labelledby={id}>
  <h2 id={id}>Writing</h2>
  <div className="a-appearance-option" data-composer-return-preference>
   <div className="a-appearance-option-description">
    <h3><label htmlFor={id+'-return'}>Return key</label></h3>
    <p id={id+'-help'}>This browser only, separate from appearance profiles. {value==='automatic'?'Automatic: '+returnKeyHelp(automatic):returnKeyHelp(mode)}</p>
    {error&&<p role="alert">{error}</p>}
   </div>
   <div className="a-appearance-control">
    <select id={id+'-return'} value={value} aria-describedby={id+'-help'} onChange={event=>set(event.target.value)}>
     <option value="automatic">Automatic (recommended)</option>
     <option value="newline">Always insert a new line</option>
    </select>
   </div>
  </div>
 </section>;
}