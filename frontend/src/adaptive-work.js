export const workViews=['focus','brief','studio'];
export function startingWorkView(presentation={}){
 return workViews.includes(presentation.workStartView)?presentation.workStartView:
  ({minimal:'focus',standard:'brief',detailed:'studio'}[presentation.interfaceDetail||presentation.executionDetail]||'brief');
}
export function homePatch(presentation={},extra={},view={}){
 return {workSurface:'home',workHomeView:view.workHomeView||startingWorkView(presentation),workHomeFilter:view.workHomeFilter||'all',
  navExpanded:view.navExpanded??false,navPinned:view.navPinned??((presentation.interfaceDetail||presentation.executionDetail)==='detailed'),...extra};
}

// Like chat reading positions, Home's scroll stays in this browser tab. Wait for
// the matching resource before restoring; a loading placeholder has no height.
export function workReadingKey(view,mode){
 return JSON.stringify([mode,view.workHomeScope||'all',view.workHomeQuery||'',view.workHomeFilter||'all',
  ...['attention','unread','working','idle'].map(key=>view.workHomePages?.[key]||0)]);
}
export function rememberHomeScroll(pane,key){
 const browser=pane.ownerDocument.defaultView,storageKey='amplifier.work-home-reading.v1';
 let storage,saved,timer;
 try{storage=browser.sessionStorage;saved=JSON.parse(storage.getItem(storageKey)||'null')}catch{}
 pane.scrollTop=saved?.key===key&&Number.isFinite(saved.top)?Math.max(0,saved.top):0;
 let top=pane.scrollTop;
 const persist=()=>{browser.clearTimeout(timer);try{storage?.setItem(storageKey,JSON.stringify({key,top}))}catch{}};
 const scroll=()=>{top=pane.scrollTop;browser.clearTimeout(timer);timer=browser.setTimeout(persist,200)};
 pane.addEventListener('scroll',scroll,{passive:true});browser.addEventListener('pagehide',persist);
 return ()=>{persist();pane.removeEventListener('scroll',scroll);browser.removeEventListener('pagehide',persist)};
}
export function currentWorkPage(value,view={},pinnedScope=null){
 return value?.scope===(pinnedScope||view.workHomeScope||'all')
  &&value?.requestedScope===(view.workHomeScope||'all')&&value?.query===(view.workHomeQuery||'')
  &&['attention','unread','working','idle'].every(key=>(value.requestedPages?.[key]||0)===(view.workHomePages?.[key]||0));
}
