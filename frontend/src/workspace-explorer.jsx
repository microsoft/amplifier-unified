import {useRegionActivity} from './activity-region';
import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight,Folder,FolderDot,MoreHorizontal,Search,FolderPlus,Pencil,Trash2,MessageCircle} from 'lucide-react';
import {NavigationRow,NavigationStatus,WorkspaceDetails,useActivityClock} from './navigation-details';
import {workspaceContext,parentPath} from './navigation-presentation';

// The server supplies the same bounded folder projection to users and agents.
// Browsing changes only the view; workspace.select changes the active project.
export function WorkspaceExplorer({state,act,onOpen,onEdit}){
 const now=useActivityClock();
 const explorer=state.workspaceExplorer||{},rows=explorer.rows||[],crumbs=explorer.breadcrumbs||[];
 const [query,setQuery]=useState(explorer.filter||''),pendingQuery=useRef(null);
 useEffect(()=>{pendingQuery.current=null;setQuery(explorer.filter||'')},[state.selectedWorkspaceId]);
 useEffect(()=>{if(pendingQuery.current===null||pendingQuery.current===explorer.filter){pendingQuery.current=null;setQuery(explorer.filter||'')}},[explorer.filter]);
 const [requests,setRequests]=useState(0),region=useRef(null);useRegionActivity(region,requests>0||!!state.sharedHistory?.loading);
 const patch=async value=>{setRequests(count=>count+1);try{return await act('view.update',{patch:value})}finally{setRequests(count=>count-1)}};
 const browse=path=>{pendingQuery.current=null;setQuery('');return patch({navWorkspacePath:path,navWorkspaceFilter:'',navWorkspacePage:1,navWorkspaceAncestorsOpen:false,navWorkspaceMode:'folders'})};
 const search=value=>{
  pendingQuery.current=value;setQuery(value);
  Promise.resolve(patch({navWorkspaceFilter:value,navWorkspacePage:1})).then(result=>{
   if(pendingQuery.current!==value)return;
   if(!result||result.accepted===false){pendingQuery.current=null;setQuery(explorer.filter||'')}
   else pendingQuery.current=null;
  }).catch(()=>{if(pendingQuery.current===value){pendingQuery.current=null;setQuery(explorer.filter||'')}});
 };
 const open=async row=>{const result=await act('workspace.select',{id:row.workspaceId});if(result&&result.accepted!==false)await onOpen?.()};
 const previous=crumbs.length>3?crumbs.slice(0,-2):[],visible=previous.length?crumbs.slice(-2):crumbs;
 const page=explorer.page||1,pages=explorer.pages||1;
 const crumbLabel=crumb=>crumb.name===crumb.path?(crumb.path.replace(/[\\/]+$/,'').split(/[\\/]/).at(-1)||crumb.name):crumb.name;
 return <section ref={region} data-activity-region="workspace-folders" className="a-workspace-explorer" data-part="workspace-explorer" aria-label="Workspace folders">
  <div className="a-workspace-modes" role="group" aria-label="Workspace list"><button type="button" aria-pressed={explorer.mode==='recent'} data-action="view.update" onClick={()=>patch({navWorkspaceMode:'recent',navWorkspacePage:1})}>Recent</button><button type="button" aria-pressed={explorer.mode!=='recent'} data-action="view.update" onClick={()=>patch({navWorkspaceMode:'folders',navWorkspacePage:1})}>Browse folders</button></div>
  <div className="a-workspace-explorer-heading"><strong>Workspaces</strong><span>{explorer.totalWorkspaces||0}</span></div>
  <div className="a-nav-search a-workspace-search"><Search/><input type="search" aria-label="Filter workspaces" placeholder="Find workspace · * ? patterns" value={query} data-action="view.update" onChange={e=>search(e.target.value)}/></div>
  {explorer.mode!=='recent'&&!explorer.filter&&<div className="a-workspace-location">
   <button type="button" className="a-icon" aria-label="Go to parent workspace folder" disabled={explorer.parentPath==null} data-action="view.update" onClick={()=>browse(explorer.parentPath)}><ArrowLeft/></button>
   <nav className="a-workspace-breadcrumbs" aria-label="Workspace folder path">
    {previous.length>0&&<div className="a-workspace-ancestors" data-open={!!state.view?.navWorkspaceAncestorsOpen}><button type="button" className="a-workspace-ancestor-toggle" aria-label="Show ancestor folders" aria-expanded={!!state.view?.navWorkspaceAncestorsOpen} data-action="view.update" onClick={()=>patch({navWorkspaceAncestorsOpen:!state.view?.navWorkspaceAncestorsOpen})}><MoreHorizontal/></button>{state.view?.navWorkspaceAncestorsOpen&&<div>{previous.map(crumb=><button key={crumb.path} type="button" data-action="view.update" aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumb.path||'All workspace folders'}</button>)}</div>}</div>}
    {visible.map((crumb,index)=><React.Fragment key={crumb.path}>{(index>0||previous.length>0)&&<ChevronRight aria-hidden="true"/>}<button type="button" data-action="view.update" aria-current={index===visible.length-1?'location':undefined} title={crumb.path||'All workspace folders'} aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumbLabel(crumb)}</button></React.Fragment>)}
   </nav>
  </div>}
  <div className="a-workspace-folders">{rows.map(row=>{
   const workspace=!!row.workspaceId,selected=workspace&&row.workspaceId===state.selectedWorkspaceId;
   const browseLabel='Browse '+row.path;
   const counts=row.activityCounts||{},summary=counts.attention?{kind:'attention',label:`${counts.attention} chats need attention`}:counts.working?{kind:'working',label:`${counts.working} chats working`}:row.unread?{kind:'unread',label:`${row.unread} chats with unread activity`}:null;
   const content=<>
    <button type="button" className="a-workspace-select" data-navigation-select data-action={workspace?'workspace.select':'view.update'} aria-label={workspace?'Open chats in '+row.path:browseLabel} aria-pressed={workspace?selected:undefined} onClick={()=>workspace?open(row):browse(row.path)}>
     {summary?<NavigationStatus activity={summary}/>:workspace?<FolderDot/>:<Folder/>}<span className="a-workspace-label"><span>{row.customName||row.name}</span><small className="a-workspace-result-path" title={row.path}>{workspaceContext(row)}</small></span>
     <span className="a-workspace-count" aria-label={`${workspace?row.chatCount:row.descendantWorkspaceCount} ${workspace?'chats':'workspaces'}`}>{workspace?row.chatCount:row.descendantWorkspaceCount}</span>{!workspace&&row.canBrowse&&<ChevronRight className="a-workspace-arrow"/>}
    </button>
    {workspace&&row.canBrowse&&<button type="button" className="a-workspace-drill" data-action="view.update" aria-label={browseLabel} title={`${row.descendantWorkspaceCount} nested workspaces`} onClick={()=>browse(row.path)}><ChevronRight/></button>}
   </>;
   return workspace?<NavigationRow className={`a-workspace-row is-workspace ${selected?'is-selected':''}`} key={row.path} data-workspace-path={row.path} label={row.customName||row.name} details={({close})=><WorkspaceDetails row={row} now={now} actions={<>
    <button type="button" className="a-link" data-action="workspace.select" onClick={()=>{close();open(row)}}><MessageCircle/>Open chats</button>
    <button type="button" data-action="view.update" onClick={()=>{close();browse(row.parentPath??parentPath(row.path))}}><Folder/>Browse parent</button>
    {onEdit&&<><button type="button" data-action="view.update" onClick={()=>{close();onEdit({mode:'rename',id:row.workspaceId,name:row.customName||row.name})}}><Pencil/>Rename</button><button type="button" className="a-danger" disabled={state.library?.workspaceCount<2} data-action="view.update" onClick={()=>{close();onEdit({mode:'remove',id:row.workspaceId,name:row.customName||row.name})}}><Trash2/>Remove</button></>}
   </>}/>} >{content}</NavigationRow>:<div className="a-workspace-row" key={row.path} data-workspace-path={row.path}>{content}</div>;

  })}</div>
  {!rows.length&&!state.sharedHistory?.loading&&<p className="a-nav-empty">{explorer.filter?'No matching workspaces.':'Create a workspace to start a chat.'}</p>}
  {pages>1&&<div className="a-nav-pagination a-workspace-pagination"><span>Page {page} of {pages}</span><div><button type="button" className="a-link" aria-label="Show previous workspace folders" data-action="view.update" disabled={page<=1} onClick={()=>patch({navWorkspacePage:page-1})}>Previous</button><button type="button" className="a-link" aria-label="Show more workspace folders" data-action="view.update" disabled={page>=pages} onClick={()=>patch({navWorkspacePage:page+1})}>More<ChevronRight/></button></div></div>}
 </section>;
}
