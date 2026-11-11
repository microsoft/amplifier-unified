// Runs synchronously before the app stylesheet or first paint. This cache only
// colors the loading screen from the committed composition, never a preview;
// the host remains authoritative for presentation.
(()=>{
 const root=document.documentElement;
 const color=value=>typeof value==='string'&&value.length<160&&(
  /^#[0-9a-f]{6}$/i.test(value)||
  /^(?:rgba?|hsla?|oklab|oklch|lab|lch|color)\([\w\d\s.,%/+*-]+\)$/i.test(value)
 )&&CSS.supports('color',value);
 const opaque=value=>{
  if(!color(value))return false;
  const parts=value.slice(0,-1).split(/[,/]/);
  const alpha=value.includes('/')||parts.length===4?parts.at(-1).trim():'1';
  return /^(?:1(?:\.0*)?|100(?:\.0*)?%)$/.test(alpha);
 };
 try{
  const saved=JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v3')||'null');
  if(saved?.version!==3||typeof saved.themeKey!=='string'||!saved.themeKey||
   !['shared','client'].includes(saved.activeScope)||typeof saved.storageVersion!=='string'||!saved.storageVersion||
   typeof saved.compositionId!=='string'||!saved.compositionId||
   !['light','dark','system'].includes(saved.scheme)||
   !saved.colors||typeof saved.colors!=='object'||Array.isArray(saved.colors))return;
  const mode=saved.scheme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):saved.scheme;
  root.style.colorScheme=mode;root.dataset.bootScheme=mode;
  const colors=saved.colors?.[mode];
  const background=opaque(colors?.background)?colors.background:mode==='dark'?'#151b31':'#e8eeff';
  root.style.setProperty('--boot-bg',background);
  root.style.setProperty('--boot-ink',color(colors?.foreground)?colors.foreground:mode==='dark'?'#eef2ff':'#263459');
  const chrome=document.querySelector('meta[name="theme-color"]');
  if(chrome)chrome.content=opaque(colors?.chrome)?colors.chrome:background;
 }catch{/* Unavailable or stale storage must never prevent the app from loading. */}
})();
