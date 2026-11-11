import React,{useEffect,useLayoutEffect,useState} from 'react';
import './theme-presentation.css';

export function useThemeScheme(requested){
 const [dark,setDark]=useState(()=>typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches);
 useEffect(()=>{const query=matchMedia('(prefers-color-scheme: dark)'),changed=()=>setDark(query.matches);changed();query.addEventListener('change',changed);return()=>query.removeEventListener('change',changed)},[]);
 return requested==='dark'||(requested==='system'&&dark)?'dark':'light';
}

// Browser chrome follows the rendered Reference composition, not a separate
// palette or definition-based theme model.
export function effectiveChromeColor(root){
 const header=root.querySelector('[data-part="header"]');
 const color=getComputedStyle(header||root).backgroundColor;
 const opaque=value=>!['transparent','rgba(0, 0, 0, 0)'].includes(value)&&(!value.startsWith('rgba(')||/,\s*1\)$/.test(value))&&(!value.includes('/')||/\/\s*1\)$/.test(value));
 if(opaque(color))return color;
 const background=getComputedStyle(root).backgroundColor;
 return opaque(background)?background:(root.dataset.themeScheme==='dark'?'#151b31':'#e8eeff');
}

export function useWindowChrome({root,shell,mode,compositionId}){
 useLayoutEffect(()=>{
  if(!root.current||!shell.ready||!compositionId)return;
  const sync=()=>{
   const app=root.current,header=app.querySelector('[data-part="header"]'),layer=app.querySelector('.a-window-chrome-blend');
   const meta=document.querySelector('meta[name="theme-color"]');
   if(meta)meta.content=effectiveChromeColor(app);
   const backdrop=getComputedStyle(app),chrome=header&&getComputedStyle(header);
   const blend=!!chrome&&chrome.backgroundColor===effectiveChromeColor(app)&&
    !['transparent','rgba(0, 0, 0, 0)'].includes(chrome.backgroundColor)&&
    backdrop.backgroundImage!=='none'&&app.dataset.decorations!=='off'&&
    chrome.backgroundImage==='none'&&chrome.backgroundColor===backdrop.backgroundColor&&
    !backdrop.getPropertyValue('--a-chrome-bg').trim()&&!chrome.getPropertyValue('--a-chrome-bg').trim();
   if(app.dataset.windowChromeBlend!==String(blend))app.dataset.windowChromeBlend=String(blend);
   if(layer){
    const color=blend?chrome.backgroundColor:'';
    if(layer.style.getPropertyValue('--a-window-chrome-color')!==color){
     if(color)layer.style.setProperty('--a-window-chrome-color',color);
     else layer.style.removeProperty('--a-window-chrome-color');
    }
   }
  };
  sync();
  const observer=new MutationObserver(sync),app=root.current;
  observer.observe(app,{attributes:true,attributeFilter:['data-theme-scheme','data-decorations','data-layout','data-density','style']});
  window.addEventListener('resize',sync);
  return()=>{observer.disconnect();window.removeEventListener('resize',sync);delete app.dataset.windowChromeBlend;app.querySelector('.a-window-chrome-blend')?.style.removeProperty('--a-window-chrome-color')};
 },[root,shell.ready,mode,compositionId]);
}

export function useAppearanceCache({root,shell,scheme,mode,preview,compositionId,activeScope,storageVersion,confirmed=false,invalidate=false}){
 useLayoutEffect(()=>{
  if(preview)return;
  let cached=null;
  try{cached=JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v3')||'null')}catch{}
  const identityChanged=!!cached&&(
   typeof activeScope==='string'&&cached.activeScope!==activeScope||
   typeof storageVersion==='string'&&cached.storageVersion!==storageVersion||
   typeof compositionId==='string'&&cached.compositionId!==compositionId);
  if(invalidate||identityChanged)try{sessionStorage.removeItem('amplifier.appearance-composition.v3')}catch{}
  if(!confirmed||!root.current||!shell.ready||!compositionId||!['shared','client'].includes(activeScope)||typeof storageVersion!=='string'||!storageVersion)return;
  const style=getComputedStyle(root.current),themeKey=String(compositionId),colors={};
  try{
   const saved=JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v3')||'null');
   if(saved?.version===3&&saved.activeScope===activeScope&&saved.storageVersion===storageVersion&&saved.compositionId===compositionId&&saved.themeKey===themeKey&&saved.colors&&typeof saved.colors==='object')Object.assign(colors,saved.colors);
  }catch{}
  colors[mode]={background:style.backgroundColor,foreground:style.color,chrome:effectiveChromeColor(root.current)};
  const appearance={version:3,activeScope,storageVersion,compositionId,themeKey,scheme:['light','dark','system'].includes(scheme)?scheme:'light',colors};
  try{sessionStorage.setItem('amplifier.appearance-composition.v3',JSON.stringify(appearance))}catch{}
  document.documentElement.style.colorScheme=mode;
  document.documentElement.style.setProperty('--boot-bg',colors[mode].background);
  document.documentElement.style.setProperty('--boot-ink',colors[mode].foreground);
 },[root,shell.ready,scheme,mode,preview,compositionId,activeScope,storageVersion,confirmed,invalidate]);
}
