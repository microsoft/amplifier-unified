import React,{useEffect,useLayoutEffect,useState} from 'react';
import './theme-presentation.css';

export function useThemeScheme(requested){
 const [dark,setDark]=useState(()=>typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches);
 useEffect(()=>{const query=matchMedia('(prefers-color-scheme: dark)'),changed=()=>setDark(query.matches);changed();query.addEventListener('change',changed);return()=>query.removeEventListener('change',changed)},[]);
 return requested==='dark'||(requested==='system'&&dark)?'dark':'light';
}

export function useAppearanceCache({root,state,shell,scheme,mode,preview,css}){
 useLayoutEffect(()=>{
  if(!root.current||!shell.ready||preview||state?.view?.themePreview||shell.data?.preview)return;
  const style=getComputedStyle(root.current);
  const colors={};
  try{Object.assign(colors,JSON.parse(sessionStorage.getItem('amplifier.appearance')||'null')?.colors)}catch{}
  colors[mode]={background:style.backgroundColor,foreground:style.color};
  const palette=state?.theme?.definition?.palette;
  if(palette)for(const key of ['light','dark'])colors[key]={background:palette[key].bg,foreground:palette[key].ink};
  const appearance={version:1,scheme:['light','dark','system'].includes(scheme)?scheme:'light',colors};
  try{sessionStorage.setItem('amplifier.appearance',JSON.stringify(appearance))}catch{}
  document.documentElement.style.colorScheme=mode;
  document.documentElement.style.setProperty('--boot-bg',colors[mode].background);
  document.documentElement.style.setProperty('--boot-ink',colors[mode].foreground);
 },[root,shell.ready,shell.data?.preview,state?.view?.themePreview,state?.theme,scheme,mode,preview,css]);
}

export function ThemeDecorationControl({shell,onError}){
 const committed=shell.composition.presentation.decorations!==false;
 const [saving,setSaving]=useState(false),[enabled,setEnabled]=useState(committed);
 useEffect(()=>{if(!saving)setEnabled(committed)},[committed,saving]);
 async function change(event){
  const next=event.target.checked;setEnabled(next);setSaving(true);
  try{await shell.setPresentation({decorations:next})}catch(error){setEnabled(committed);onError(error.message)}finally{setSaving(false)}
 }
 return <label className="a-inline-checkbox"><input type="checkbox" checked={enabled} disabled={saving} data-action="shell.changes.apply" onChange={change}/>Show decorative theme background</label>;
}
