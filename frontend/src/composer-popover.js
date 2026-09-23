import {useLayoutEffect,useState} from 'react';
// Keep the compact control inside the viewport, independent of composer width.
export function useComposerPopover(open,ref){
 const [style,setStyle]=useState({visibility:'hidden'});
 useLayoutEffect(()=>{
  if(!open)return;
  const panel=ref.current?.querySelector('.a-compact-popover'),anchor=ref.current?.querySelector('button');
  if(!panel||!anchor)return;
  const place=()=>{const a=anchor.getBoundingClientRect(),width=Math.min(340,window.innerWidth-24),height=panel.getBoundingClientRect().height;
   setStyle({width,left:Math.max(12,Math.min(a.left,window.innerWidth-width-12)),top:Math.max(12,Math.min(a.top-height-10,window.innerHeight-height-12)),visibility:'visible'});};
  place();const observer=new ResizeObserver(place);observer.observe(panel);window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)};
 },[open,ref]);
 return style;
}
