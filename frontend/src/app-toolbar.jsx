import React,{useEffect,useRef} from 'react';
import {Bug,Ellipsis,Palette,ScanEye} from 'lucide-react';
import {AttentionBadge} from './attention';
import './app-toolbar.css';

export function MoreAppActions({state,act,openPanel}){
 const root=useRef(null),trigger=useRef(null);
 const expanded=!!state.view?.toolbarMenuOpen;
 const close=()=>act('view.update',{patch:{toolbarMenuOpen:false}});
 useEffect(()=>{
  if(!expanded)return;
  root.current?.querySelector('.a-toolbar-menu button')?.focus();
  const outside=event=>{if(root.current&&!root.current.contains(event.target))close()};
  const escape=event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();trigger.current?.focus();close()}};
  document.addEventListener('pointerdown',outside);
  document.addEventListener('focusin',outside);
  document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('focusin',outside);document.removeEventListener('keydown',escape)};
 },[expanded,act]);
 const choose=panel=>{trigger.current?.focus();openPanel(panel)};
 return <div className="a-toolbar-more" ref={root}>
  <button ref={trigger} type="button" className="a-icon" aria-label="More app options" aria-expanded={expanded} aria-controls="app-toolbar-menu" data-action="view.update" onClick={()=>act('view.update',{patch:{toolbarMenuOpen:!expanded}})}><Ellipsis/><AttentionBadge state={state} section="feedback"/></button>
  {expanded&&<div id="app-toolbar-menu" className="a-toolbar-menu" role="group" aria-label="More app options">
   <button type="button" data-action="view.update" onClick={()=>choose('appearance')}><Palette/>Customize appearance</button>
   <button type="button" data-action="view.update" onClick={()=>choose('agent')}><ScanEye/>What the agent sees</button>
   <button type="button" data-action="view.update" aria-label="Send feedback" onClick={()=>choose('feedback')}><Bug/>Send feedback<AttentionBadge state={state} section="feedback"/></button>
  </div>}
 </div>;
}
