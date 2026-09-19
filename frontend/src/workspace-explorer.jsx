import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight,Folder,FolderDot,MoreHorizontal,Search} from 'lucide-react';
import {AttentionBadge} from './attention';

// The server supplies the same bounded folder projection to users and agents.
// Browsing changes only the view; workspace.select changes the active project.
export function WorkspaceExplorer({state,act}){
 const explorer=state.workspaceExplorer||{},rows=explorer.rows||[],crumbs=explorer.breadcrumbs||[];
 const [query,setQuery]=useState(explorer.filter||''),pendingQuery=useRef(null);
 useEffect(()=>{pendingQuery.current=null;setQuery(explorer.filter||'')},[state.selectedWorkspaceId]);
 useEffect(()=>{if(pendingQuery.current===null||pendingQuery.current===explorer.filter){pendingQuery.current=null;setQuery(explorer.filter||'')}},[explorer.filter]);
 const patch=value=>act('view.update',{patch:value});
 const browse=path=>{pendingQuery.current=null;setQuery('');return patch({navWorkspacePath:path,navWorkspaceFilter:'',navWorkspacePage:1,navWorkspaceAncestorsOpen:false})};
 const search=value=>{
  pendingQuery.current=value;setQuery(value);
  Promise.resolve(patch({navWorkspaceFilter:value,navWorkspacePage:1})).then(result=>{
   if(pendingQuery.current!==value)return;
   if(!result||result.accepted===false){pendingQuery.current=null;setQuery(explorer.filter||'')}
   else pendingQuery.current=null;
  }).catch(()=>{if(pendingQuery.current===value){pendingQuery.current=null;setQuery(explorer.filter||'')}});
 };
 const previous=crumbs.length>3?crumbs.slice(0,-2):[],visible=previous.length?crumbs.slice(-2):crumbs;
 const page=explorer.page||1,pages=explorer.pages||1;
 const crumbLabel=crumb=>crumb.name===crumb.path?(crumb.path.replace(/[\\/]+$/,'').split(/[\\/]/).at(-1)||crumb.name):crumb.name;
 return <section className="a-workspace-explorer" data-part="workspace-explorer" aria-label="Workspace folders">
  <div className="a-workspace-explorer-heading"><strong>Workspaces</strong><span>{explorer.totalWorkspaces||0}</span></div>
  <div className="a-nav-search a-workspace-search"><Search/><input type="search" aria-label="Filter workspaces" placeholder="Find workspace · * ? patterns" value={query} data-action="view.update" onChange={e=>search(e.target.value)}/></div>
  <div className="a-workspace-location">
   <button type="button" className="a-icon" aria-label="Go to parent workspace folder" disabled={explorer.parentPath==null} data-action="view.update" onClick={()=>browse(explorer.parentPath)}><ArrowLeft/></button>
   <nav className="a-workspace-breadcrumbs" aria-label="Workspace folder path">
    {previous.length>0&&<div className="a-workspace-ancestors" data-open={!!state.view?.navWorkspaceAncestorsOpen}><button type="button" className="a-workspace-ancestor-toggle" aria-label="Show ancestor folders" aria-expanded={!!state.view?.navWorkspaceAncestorsOpen} data-action="view.update" onClick={()=>patch({navWorkspaceAncestorsOpen:!state.view?.navWorkspaceAncestorsOpen})}><MoreHorizontal/></button>{state.view?.navWorkspaceAncestorsOpen&&<div>{previous.map(crumb=><button key={crumb.path} type="button" data-action="view.update" aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumb.path||'All workspace folders'}</button>)}</div>}</div>}
    {visible.map((crumb,index)=><React.Fragment key={crumb.path}>{(index>0||previous.length>0)&&<ChevronRight aria-hidden="true"/>}<button type="button" data-action="view.update" aria-current={index===visible.length-1?'location':undefined} title={crumb.path||'All workspace folders'} aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumbLabel(crumb)}</button></React.Fragment>)}
   </nav>
  </div>
  <div className="a-workspace-folders">{rows.map(row=>{
   const workspace=!!row.workspaceId,selected=workspace&&row.workspaceId===state.selectedWorkspaceId;
   const browseLabel='Browse '+row.path;
   return <div className={`a-workspace-row ${workspace?'is-workspace':''} ${selected?'is-selected':''}`} key={row.path} data-workspace-path={row.path}>
    <button type="button" className="a-workspace-select" data-action={workspace?'workspace.select':'view.update'} aria-label={workspace?'Open chats in '+row.path:browseLabel} aria-pressed={workspace?selected:undefined} title={row.path} onClick={()=>workspace?act('workspace.select',{id:row.workspaceId}):browse(row.path)}>
     {workspace?<FolderDot/>:<Folder/>}<span className="a-workspace-label"><span>{row.name}</span>{row.customName&&<small>{row.customName}</small>}{explorer.filter&&<small className="a-workspace-result-path">{row.path}</small>}</span>
     <span className="a-workspace-count">{workspace?`${row.chatCount} ${row.chatCount===1?'chat':'chats'}`:`${row.descendantWorkspaceCount} ${row.descendantWorkspaceCount===1?'workspace':'workspaces'}`}</span><AttentionBadge state={state} count={row.unread}/>{!workspace&&row.canBrowse&&<ChevronRight className="a-workspace-arrow"/>}
    </button>
    {workspace&&row.canBrowse&&<button type="button" className="a-workspace-drill" data-action="view.update" aria-label={browseLabel} title={`${row.descendantWorkspaceCount} nested ${row.descendantWorkspaceCount===1?'workspace':'workspaces'}`} onClick={()=>browse(row.path)}><ChevronRight/></button>}
   </div>;
  })}</div>
  {!rows.length&&!state.sharedHistory?.loading&&<p className="a-nav-empty">{explorer.filter?'No matching workspaces.':'Create a workspace to start a chat.'}</p>}
  {pages>1&&<div className="a-nav-pagination a-workspace-pagination"><span>Page {page} of {pages}</span><div><button type="button" className="a-link" aria-label="Show previous workspace folders" data-action="view.update" disabled={page<=1} onClick={()=>patch({navWorkspacePage:page-1})}>Previous</button><button type="button" className="a-link" aria-label="Show more workspace folders" data-action="view.update" disabled={page>=pages} onClick={()=>patch({navWorkspacePage:page+1})}>More<ChevronRight/></button></div></div>}
 </section>;
}
