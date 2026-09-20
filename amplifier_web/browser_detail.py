"""Bounded browser views. Full history remains in the existing session store."""
from copy import deepcopy
from hashlib import sha256

from .execution import LIVE_PHASES

MESSAGE_LIMIT=60
NODE_LIMIT=100
TEXT_LIMIT=4096
SUMMARY_LIMIT=512


def digest(text):return sha256(text.encode()).hexdigest()

def compact(row, session_id, part, limit):
    fields = {'id','parentId','turnId','sessionId','rootSessionId','kind','phase','status','label','tool','toolCallId','workerId','callId','call_id','provider','model','startedAt','endedAt','updatedAt','createdAt','usage','aggregateUsage','summary','detail','name','agent','report','result','persistent','event','parentSessionId','retryAttempt','retryMax','input','output','error'}
    result = {key:value for key,value in row.items() if part=='messages' or key in fields}
    for field in ('text','summary','detail','report','result','input','output','error'):
        text=row.get(field)
        if part!='messages' and field in result and not isinstance(text,str):
            result.pop(field, None)
        if isinstance(text,str) and len(text)>limit:
            result[field]=text[:limit]
            result[field+'Detail']={'sessionId':session_id,'part':part,'id':row.get('id'),'field':field,'digest':digest(text),'length':len(text)}
    return result


def page(session, part, before=None):
    if part not in {'messages','nodes'}:raise ValueError('Choose messages or nodes.')
    rows=session.get('messages',[]) if part=='messages' else session.get('execution',{}).get('nodes',[])
    end=len(rows)
    if before is not None:
        end=next((i for i,row in enumerate(rows) if row.get('id')==before),None)
        if end is None:raise ValueError('This history changed. Return to the latest messages and try again.')
    start=max(0,end-(MESSAGE_LIMIT if part=='messages' else NODE_LIMIT))
    items=[compact(row,session['id'],part,TEXT_LIMIT if part=='messages' else SUMMARY_LIMIT) for row in rows[start:end]]
    result={'items':items,'offset':start,'total':len(rows),'before':items[0]['id'] if start and items else None}
    if part=='messages':
        result['userOffset']=session.get('sharedHistoryUserTurnOffset',0)+sum(row.get('role')=='user' for row in rows[:start])
    else:
        turn_ids={row.get('turnId') for row in items}
        # Keep newly started work visible before its first tool/provider event.
        active=[row['id'] for row in session.get('execution',{}).get('turns',[]) if row.get('phase') in LIVE_PHASES and not row.get('endedAt')]
        turn_ids.update(active[-20:])
        counts={identity:{'tools':0,'workers':0} for identity in turn_ids}
        for node in rows:
            if node.get('turnId') in counts and node.get('kind') in {'tool','worker'}:
                counts[node['turnId']]['tools' if node['kind']=='tool' else 'workers']+=1
        result['turns']=[{**row,'nodeCounts':counts[row['id']]} for row in session.get('execution',{}).get('turns',[]) if row['id'] in turn_ids]
    return deepcopy(result)


def project(session):
    result=dict(session)
    messages=page(session,'messages');nodes=page(session,'nodes')
    result.update(messages=messages.pop('items'),messageWindow=messages,
                  sharedHistoryUserTurnOffset=messages['userOffset'])
    if session.get('nativeProject') and session.get('historyManaged'):
        # Native history already has its own bounded, user-controlled loader.
        result['messages']=[compact(row,session['id'],'messages',TEXT_LIMIT) for row in session.get('messages',[])]
        result.pop('messageWindow',None)
        result['sharedHistoryUserTurnOffset']=session.get('sharedHistoryUserTurnOffset',0)
    if 'execution' in session:
        result['execution']={**session['execution'],'nodes':nodes.pop('items'),'turns':nodes.pop('turns')}
        result['executionWindow']=nodes
    # Reports and completed generation bodies are not activity badges.
    result['workers']=[compact(row,session['id'],'workers',SUMMARY_LIMIT) for row in session.get('workers',[])]
    result['generations']=[{k:v for k,v in row.items() if k!='text'} for row in session.get('generations',[])[-20:]]
    if 'historyActivity' in result:
        result['historyActivity']={'diagnostics':result['historyActivity'].get('diagnostics',[])}
    return result


def read_text(session, args):
    part=args.get('part');field=args.get('field')
    if part not in {'messages','nodes','workers'} or field not in {'text','summary','detail','report','result','input','output','error'}:
        raise ValueError('Choose a valid detail field.')
    rows=session.get('execution',{}).get('nodes',[]) if part=='nodes' else session.get(part,[])
    row=next((row for row in rows if row.get('id')==args.get('id')),None)
    text=row.get(field) if row else None
    if not isinstance(text,str):raise ValueError('This detail is no longer available.')
    if digest(text)!=args.get('digest'):raise ValueError('This detail changed. Close and reopen it to load the current version.')
    offset=int(args.get('offset',0))
    if offset<0 or offset>len(text):raise ValueError('Invalid detail offset.')
    end=min(len(text),offset+16000)
    return {'value':text[offset:end],'nextOffset':end if end<len(text) else None,'total':len(text)}
