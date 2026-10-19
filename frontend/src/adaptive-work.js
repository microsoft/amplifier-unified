export const workViews=['focus','brief','studio'];
export function startingWorkView(presentation={}){
 return workViews.includes(presentation.workStartView)?presentation.workStartView:
  ({minimal:'focus',standard:'brief',detailed:'studio'}[presentation.interfaceDetail||presentation.executionDetail]||'brief');
}
export function homePatch(presentation={},extra={},view={}){
 return {workSurface:'home',workHomeView:startingWorkView(presentation),workHomeFilter:'all',
  navExpanded:view.navExpanded??false,navPinned:view.navPinned??((presentation.interfaceDetail||presentation.executionDetail)==='detailed'),...extra};
}
export function currentWorkPage(value,view={}){
 return value?.scope===(view.workHomeScope||'all')&&value?.query===(view.workHomeQuery||'')
  &&['attention','unread','working','idle'].every(key=>(value.requestedPages?.[key]||0)===(view.workHomePages?.[key]||0));
}
