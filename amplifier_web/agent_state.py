"""Bounded app-state previews with lossless, revision-aware JSON Pointer paging.

The browser receives bounded navigation pages. Agents can read the full catalog;
responses point to data they omit;
large mount plans and model catalogs must not swamp the model's context.
"""
import json


def _pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _preview(value, path, budget=2500, depth=0):
    if _size(value) <= budget:
        return value
    ref = {'$statePath':path,'type':type(value).__name__,'size':len(value) if isinstance(value,(dict,list,str)) else None}
    if depth >= 4 or budget < 250:
        return ref
    if isinstance(value, str):
        return {**ref,'preview':value[:max(0,budget-180)]}
    if isinstance(value, list):
        count = min(5,len(value))
        return {**ref,'preview':[_preview(v,_pointer(path,i),max(100,(budget-250)//count),depth+1) for i,v in enumerate(value[:count])]}
    if isinstance(value, dict):
        keys=list(value)[:10]
        return {**ref,'preview':{k:_preview(value[k],_pointer(path,k),max(100,(budget-250)//len(keys)),depth+1) for k in keys}}
    return ref


def overview(state, session_id):
    from .session_navigation import is_top_level
    selected = session_id or state.get('selectedSessionId')
    index = next((i for i,s in enumerate(state.get('sessions', [])) if s['id']==selected), None)
    core = {key:_preview(state[key],'/'+key,3000) for key in ['revision','selectedSessionId','selectedWorkspaceId','workspaces','workspaceExplorer','chatNavigation','headerChatNavigation','subagentNavigation','pinnedSessionIds','sharedHistory','canvas','view','attention','voice'] if key in state}
    core['canvasArtifacts'] = {'items':[{**{k:r.get(k) for k in ('id','title','kind','messageId','tabOpen')},'$statePath':f'/canvasArtifacts/{i}'} for i,r in list(enumerate(state.get('canvasArtifacts',[]))) if r.get('sessionId')==selected][-20:],'total':sum(r.get('sessionId')==selected for r in state.get('canvasArtifacts',[])),'$statePath':'/canvasArtifacts'}
    core['diagnostics'] = _preview(state.get('diagnostics', {}), '/diagnostics', 1200)
    core['smartTools'] = _preview(state.get('smartTools', {}), '/smartTools', 2000)
    core['session'] = None
    if index is not None:
        session=state['sessions'][index];base=f'/sessions/{index}'
        core['session']={key:session.get(key) for key in ['id','title','status','bundle','workspace','workspaceId','sessionKind','nativeParentId','historyLoaded','historyReadOnlyReason','sharedHistoryOffset','sharedHistoryUserTurnOffset','sharedHistoryTotal','recentActivityAt']}
        core['session']['pinned'] = selected in state.get('pinnedSessionIds', [])
        core['session'].update({'$statePath':base,'activity':_preview(session.get('activity',{}),base+'/activity',1500),
            'workers':_preview(session.get('workers',[]),base+'/workers',1500),
            'recentMessages':[_preview(message,_pointer(base+'/messages',i),1000) for i,message in list(enumerate(session.get('messages',[])))[-6:]],
            'messageCount':len(session.get('messages',[])),
            'model':_preview(state.get('runtimeControl',{}).get(selected,{}).get('configuration.providers',{}).get('effective'),_pointer('/runtimeControl',selected)+'/configuration.providers/effective',1000)})
    if state.get('devices'):
        device_id,device=max(state['devices'].items(),key=lambda pair:pair[1].get('updatedAt',0))
        core['visibleUI']=_preview(device,_pointer('/devices',device_id),4000)
    core['conversations'] = [{'id':s['id'],'title':s.get('title'),'status':s.get('status'),'messageCount':s.get('sharedHistoryTotal',len(s.get('messages',[])) if s.get('historyLoaded',True) else None),'historyLoaded':s.get('historyLoaded',True),'workspaceId':s.get('workspaceId'),'$statePath':f'/sessions/{i}'} for i,s in enumerate(state.get('sessions',[])) if is_top_level(s)][:50]
    children = [(i,s) for i,s in enumerate(state.get('sessions',[])) if not is_top_level(s) and s.get('parentId') == selected]
    core['subagentChats'] = {'total':len(children), 'items':[{'id':s['id'],'title':s.get('title'),'$statePath':f'/sessions/{i}'} for i,s in children[:20]]}
    core['_stateAccess'] = {'note':'This overview is scoped to the calling session. Other app state and detailed resources are available by JSON Pointer; pass path, offset, limit and optional revision. Follow nextOffset. Text previews and $resource references are not the complete value.',
        'history':'CLI workspaces and top-level chats are discovered automatically. /workspaceExplorer is the folder navigation shown to the user: only existing workspaces with top-level chats and their ancestor folders appear. Rows with workspaceId can be selected with workspace.select; rows with canBrowse can be opened with view.update {patch:{navWorkspacePath:path}} without switching the conversation. Search all workspace paths or aliases using navWorkspaceFilter (case-insensitive fnmatch or plain text), navigate 1-based pages with navWorkspacePage, and toggle the ancestor menu with navWorkspaceAncestorsOpen. Browsing persists until a different workspace is selected. workspace.create {path,name?} creates or chooses a folder and opens its first chat without starting model work. Missing or empty folders stay in /workspaces; history.refresh rechecks availability. Subagent histories remain in /sessions with sessionKind=worker and parentId; they are omitted from the conversation list. Unloaded transcripts have no message count yet. Use history.refresh, session.select, and session.history {id,before,limit} to refresh or read earlier messages. Respect historyReadOnlyReason before continuing saved worker or legacy sessions.',
        'chats':'/chatNavigation is the same bounded chat list shown to the user: pins first, then recent conversation activity. Use view.update {patch:{navChatScope:"all"}} for top-level chats across available workspaces or navChatScope:"workspace" for the selected workspace. navFilter searches titles, descriptions, IDs and full workspace paths or names using case-insensitive fnmatch or plain text. Browse pages with navChatPage:{...chatNavigation.scope,index} (zero-based). session.pin {id,pinned:true|false} pins or unpins a root chat; pinnedSessionIds are app preferences and never change its shared transcript. Selecting, renaming or pinning a chat does not make it recent. /headerChatNavigation is the selected workspace menu. /subagentNavigation pages direct children: use view.update {patch:{panel:"subagent-history",subagentHistory:{sessionId,filter,index}}}; index is zero-based, pages hold 50. Browser snapshots contain only visible catalog pages; full /sessions and /workspaces remain available through JSON Pointer paging, and actions accept IDs outside the displayed page.',
        'revision':state.get('revision'),'sections':[{'name':key,'path':'/'+key} for key in state]}
    return core


def read_state(state, args, *, session_id=None, resolve=None):
    if not isinstance(args,dict):
        raise ValueError('State arguments must be an object.')
    path=args.get('path')
    revision=args.get('revision')
    if revision is not None and revision != state.get('revision'):
        raise ValueError('App state changed. Read the current revision before continuing pages.')
    if path is None:
        return overview(state, session_id)
    if not isinstance(path,str) or (path and not path.startswith('/')):
        raise ValueError('path must be a JSON Pointer, for example /canvas or /sessions/0/messages.')
    value=state
    try:
        for token in path.split('/')[1:] if path else []:
            if resolve and isinstance(value,dict) and '$resource' in value:
                value=resolve(value['$resource'])
            token=token.replace('~1','/').replace('~0','~')
            value=value[int(token)] if isinstance(value,list) else value[token]
    except (KeyError,IndexError,TypeError,ValueError):
        raise ValueError('That state path does not exist.') from None
    if resolve and isinstance(value,dict) and '$resource' in value:
        value=resolve(value['$resource'])
    offset=args.get('offset',0);limit=args.get('limit',50)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=16000:
        raise ValueError('Use a nonnegative integer offset and a limit between 1 and 16000.')
    result={'path':path,'revision':state.get('revision'),'offset':offset,'nextOffset':None}
    if isinstance(value,str):
        end=min(len(value),offset+min(limit if 'limit' in args else 16000,16000))
        return {**result,'type':'string','value':value[offset:end],'total':len(value),'nextOffset':end if end<len(value) else None}
    if isinstance(value,(list,dict)):
        items=list(value.items()) if isinstance(value,dict) else list(enumerate(value))
        selected=items[offset:offset+min(limit,50)];rows=[];used=0
        for key,item in selected:
            preview=_preview(item,_pointer(path,key),2500)
            row={'key':key,'path':_pointer(path,key),'value':preview};size=_size(row)
            if rows and used+size>24000:break
            rows.append(row);used+=size
        end=offset+len(rows)
        return {**result,'type':'object' if isinstance(value,dict) else 'array','items':rows,'total':len(items),'nextOffset':end if end<len(items) else None}
    return {**result,'type':'scalar','value':value}
