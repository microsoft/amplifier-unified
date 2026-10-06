import React,{useEffect,useLayoutEffect,useState} from 'react';
import './theme-presentation.css';

export function useThemeScheme(requested){
 const [dark,setDark]=useState(()=>typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches);
 useEffect(()=>{const query=matchMedia('(prefers-color-scheme: dark)'),changed=()=>setDark(query.matches);changed();query.addEventListener('change',changed);return()=>query.removeEventListener('change',changed)},[]);
 return requested==='dark'||(requested==='system'&&dark)?'dark':'light';
}

// Read the painted header rather than palette metadata: legacy CSS skins and
// previews need not have a structured theme definition. Browsers expect a
// resolved opaque color, not a var()/light-dark() expression or a gradient.
export function effectiveChromeColor(root){
 const header=root.querySelector('[data-part="header"]');
 const color=getComputedStyle(header||root).backgroundColor;
 const opaque=value=>!['transparent','rgba(0, 0, 0, 0)'].includes(value)&&(!value.startsWith('rgba(')||/,\s*1\)$/.test(value))&&(!value.includes('/')||/\/\s*1\)$/.test(value));
 if(opaque(color))return color;
 const background=getComputedStyle(root).backgroundColor;
 return opaque(background)?background:(root.dataset.themeScheme==='dark'?'#151b31':'#e8eeff');
}

export function useWindowChrome({root,state,shell,mode,css}){
 useLayoutEffect(()=>{
  if(!root.current||!shell.ready)return;
  const sync=()=>{
   const meta=document.querySelector('meta[name="theme-color"]');
   if(meta)meta.content=effectiveChromeColor(root.current);
  };
  sync();
  // Focused Canvas, custom header slots and portal dialogs may change painted
  // chrome. Observe only the root's presentation, not every message mutation.
  const observer=new MutationObserver(sync);
  observer.observe(root.current,{attributes:true,attributeFilter:['data-theme-scheme','style']});
  return()=>observer.disconnect();
 },[root,shell.ready,state?.theme,mode,css]);
}

export function useAppearanceCache({root,state,shell,scheme,mode,preview,css}){
 useLayoutEffect(()=>{
  if(!root.current||!shell.ready||preview||state?.view?.themePreview||shell.data?.preview)return;
  const style=getComputedStyle(root.current);
  // This hash is only a cache-generation key, never an integrity credential.
  // Otherwise an unvisited light palette could retain the previous skin's
  // chrome color after committing a new dark-only appearance.
  let hash=0;for(let i=0;i<css.length;i++)hash=(Math.imul(hash,31)+css.charCodeAt(i))|0;
  const themeKey=css.length+':'+hash,colors={};
  try{const saved=JSON.parse(sessionStorage.getItem('amplifier.appearance')||'null');if(saved?.themeKey===themeKey)Object.assign(colors,saved.colors)}catch{}
  colors[mode]={background:style.backgroundColor,foreground:style.color,chrome:effectiveChromeColor(root.current)};
  const palette=state?.theme?.definition?.palette;
  if(palette)for(const key of ['light','dark'])colors[key]={background:palette[key].bg,foreground:palette[key].ink,chrome:key===mode?colors[key].chrome:palette[key].bg};
  const appearance={version:1,themeKey,scheme:['light','dark','system'].includes(scheme)?scheme:'light',colors};
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
