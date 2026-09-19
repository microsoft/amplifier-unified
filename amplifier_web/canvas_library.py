"""Durable canvas snapshots. Only the active body travels with regular app state."""
import copy
import hashlib
import json
import time
import uuid

from .state_storage import resource


def remember(state, db):
    rows = state.setdefault('canvasArtifacts', [])
    canvas = state.get('canvas', {})
    if not canvas.get('kind') or canvas.get('placeholder'):
        return
    if 'sessionId' not in canvas:  # Migrate the one legacy preview.
        canvas['sessionId'] = state.get('selectedSessionId')
    session = next((s for s in state.get('sessions', []) if s['id'] == canvas['sessionId']), {})
    if 'messageId' not in canvas:
        canvas['messageId'] = next((m['id'] for m in reversed(session.get('messages', [])) if m.get('role')=='user' and m.get('id')), None)
    canvas.setdefault('createdAt', time.time())
    reference=canvas.get('contentResource')
    if not reference:
        # Connection/context/operation IDs change on every poll; they must never
        # become part of the static HTML content identity.
        body = {key:canvas[key] for key in ('content','surface') if key in canvas}
        from .resource_files import put
        reference=put(db,body)
        if canvas.get('kind') in {'html','babylon'} and len(canvas.get('content','').encode())>1_000_000:
            # The immutable snapshot stays in the artifact store, not SSE, action
            # receipts, the agent context, or every subsequent state write.
            canvas['contentResource']=reference
            canvas.pop('content',None)
    previous = next((r for r in rows if r['id']==canvas['id']), None)
    record = {key:copy.deepcopy(canvas[key]) for key in ('id','title','kind','path','url','sessionId','workspaceId','messageId','createdAt','view','events','sharedToolView','contentResource') if key in canvas}
    if 'mcp' in canvas:
        from .resource_files import put
        record['mcpState'] = put(db, canvas['mcp'])
    record['body'] = copy.deepcopy(reference)
    record['tabOpen'] = previous.get('tabOpen', True) if previous else True
    if previous:
        previous.update(record)
    else:
        rows.append(record)


def scope(state, row):
    return row.get('sessionId') == state.get('selectedSessionId') and row.get('workspaceId') == state.get('selectedWorkspaceId')


def load(state, db, identity, *, open_panel=True):
    from .service import AppError
    row = next((r for r in state.get('canvasArtifacts',[]) if r['id']==identity and scope(state,r)), None)
    if not row:
        raise AppError('This artifact belongs to another chat or is no longer available.')
    canvas = {**copy.deepcopy(row), **({} if row.get('contentResource') else resource(db,row['body']['$resource'])), 'open':open_panel, 'renderReports':{}}
    canvas.pop('body',None)
    canvas.pop('tabOpen',None)
    mcp_state = canvas.pop('mcpState', None)
    if mcp_state:
        canvas['mcp'] = resource(db,mcp_state['$resource'])
    row['tabOpen'] = True
    row['lastViewedAt'] = time.time()
    state['canvas'] = canvas


def empty(state, *, open_panel=False):
    state['canvas'] = {'id':uuid.uuid4().hex,'open':open_panel,'placeholder':True,'events':[],
                       'sessionId':state.get('selectedSessionId'),'workspaceId':state.get('selectedWorkspaceId')}


def command(state, db, action, args):
    from .service import AppError
    remember(state,db)
    if action=='canvas.select':
        load(state,db,args['id'])
    elif action=='canvas.reopen':
        current=state.get('canvas',{})
        if scope(state,current):
            current['open']=True
            row=next((r for r in state['canvasArtifacts'] if r['id']==current['id']),None)
            if row:row['tabOpen']=True
        else:
            restore(state,db,open_panel=True)
    elif action=='canvas.tabClose':
        row=next((r for r in state['canvasArtifacts'] if r['id']==args['id'] and scope(state,r)),None)
        if not row:raise AppError('Canvas tab is unavailable.')
        row['tabOpen']=False
        if state['canvas'].get('id')==row['id']:
            remaining=[r for r in state['canvasArtifacts'] if scope(state,r) and r.get('tabOpen')]
            if remaining:load(state,db,remaining[-1]['id'])
            else:empty(state,open_panel=True)


def restore(state,db,*,open_panel=False):
    rows=[r for r in state.get('canvasArtifacts',[]) if scope(state,r) and r.get('tabOpen')]
    if rows:load(state,db,max(rows,key=lambda r:r.get('lastViewedAt',r.get('createdAt',0)))['id'],open_panel=open_panel)
    else:empty(state,open_panel=open_panel)


def fork_artifacts(state, source_id, target):
    """Carry snapshots only through retained messages; never carry future artifacts."""
    kept={m['id'] for m in target.get('messages',[]) if m.get('id')}
    for row in list(state.get('canvasArtifacts',[])):
        if row.get('sessionId')==source_id and row.get('messageId') in kept:
            state['canvasArtifacts'].append({**copy.deepcopy(row),'id':uuid.uuid4().hex,'sessionId':target['id'],'tabOpen':False,**({'sharedToolView':True} if row.get('kind')=='mcp-app' else {})})


def recover_legacy(state, db, home):
    """Recover accepted inline publications from checkpoints without replaying tools."""
    if state.get('canvasLibraryMigration'):
        return
    from .host.storage import SessionStore
    from .session_store import text_content
    from .workspace_canvas import canvas_command
    remember(state,db)
    recovered=0
    for session in state.get('sessions',[]):
        try:
            store=SessionStore.for_app(home,session['workspace'])
            store._migrate(session.get('runtimeSessionId') or session['id'])
            path=store.directory(session.get('runtimeSessionId') or session['id']) / 'transcript.jsonl'
            if not path.is_file() or path.stat().st_size>64_000_000:continue
            saved=store.load(session.get('runtimeSessionId') or session['id'])
            if not saved:continue
            messages=saved[0]
        except (OSError,ValueError):
            continue
        workspace=next((w for w in state.get('workspaces',[]) if w['path']==session.get('workspace')),None)
        if not workspace:continue
        receipts={m.get('tool_call_id'):m for m in messages if m.get('role')=='tool'}
        visible=[m for m in session.get('messages',[]) if m.get('role')=='user']
        cursor=0;message_id=None
        for message in messages:
            if message.get('role')=='user' and cursor<len(visible):
                content=message.get('content')
                first=content[0].get('text') if isinstance(content,list) and content and isinstance(content[0],dict) else None
                if text_content(message)==visible[cursor].get('text') or (visible[cursor].get('attachments') and first==visible[cursor].get('text')):
                    message_id=visible[cursor]['id'];cursor+=1
            for call in message.get('tool_calls',[]) or []:
                function=call.get('function') or call
                if (function.get('name') or call.get('tool'))!='app_control':continue
                try:
                    args=function.get('arguments',{})
                    if isinstance(args,str):args=json.loads(args)
                    if args.get('operation') not in {'dispatch','action.dispatch'} or args.get('args',{}).get('action')!='canvas.show':continue
                    result=receipts.get(call.get('id'),{}).get('content','{}')
                    if isinstance(result,str):result=json.loads(result)
                    if not isinstance(result,dict) or result.get('error') or not result.get('output',result).get('accepted'):continue
                    payload=args['args']['args']
                    if payload.get('path'):continue  # Never invent an old file snapshot by rereading today's file.
                    temporary={**state,'selectedSessionId':session['id'],'selectedWorkspaceId':workspace['id'],'canvas':{}}
                    canvas_command(temporary,'canvas.show',payload,'migration')
                    canvas=temporary['canvas']
                    canvas.update(id=uuid.uuid5(uuid.NAMESPACE_URL,session['id']+str(call.get('id'))).hex,messageId=message_id)
                    existing=next((r for r in state['canvasArtifacts'] if r.get('sessionId')==session['id'] and r.get('title')==canvas.get('title') and r.get('messageId')==message_id and resource(db,r['body']['$resource'])=={k:canvas[k] for k in ('content','surface','mcp') if k in canvas}),None)
                    if existing:continue
                    remember(temporary,db)
                    state['canvasArtifacts'][-1]['tabOpen']=False
                    recovered+=1
                except (ValueError,TypeError,KeyError,AttributeError):
                    continue
                except Exception as exc:
                    from .service import AppError
                    if not isinstance(exc,AppError):raise
    state['canvasLibraryMigration']={'recovered':recovered,'at':time.time()}
