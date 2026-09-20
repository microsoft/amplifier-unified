// Load a page only when the user moves toward older history, never on mount.
export function followEarlierHistory(pane,canLoad,load){
 let previous=pane.scrollTop,busy=false,disposed=false;
 const scroll=()=>{
  const top=pane.scrollTop,up=top<previous;previous=top;
  if(disposed||busy||!up||top>120||!canLoad())return;
  busy=true;Promise.resolve().then(load).finally(()=>{busy=false;previous=pane.scrollTop});
 };
 pane.addEventListener('scroll',scroll,{passive:true});
 return ()=>{disposed=true;pane.removeEventListener('scroll',scroll)};
}
