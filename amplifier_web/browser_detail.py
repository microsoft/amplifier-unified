"""Bounded browser views. Full history remains in the existing session store."""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256

from .execution import LIVE_PHASES, rollup

MESSAGE_LIMIT=60
NODE_LIMIT=100
TEXT_LIMIT=4096
SUMMARY_LIMIT=512


def digest(text):return sha256(text.encode()).hexdigest()

def compact(row, session_id, part, limit):
    fields = {'anchorMessageId','id','parentId','turnId','sessionId','rootSessionId','kind','phase','status','label','tool','toolCallId','workerId','callId','call_id','provider','model','startedAt','endedAt','updatedAt','createdAt','usage','aggregateUsage','summary','detail','name','agent','report','result','persistent','event','parentSessionId','retryAttempt','retryMax','input','output','error','lifecycle','requestInfo'}
    result = {key:value for key,value in row.items() if part=='messages' or key in fields}
    for field in ('input', 'output', 'error', 'request'):
        if row.get('_eventFields', {}).get(field) and row.get(field + 'Detail'):
            result[field + 'Detail'] = row[field + 'Detail']
    for field in ('text','summary','detail','report','result','input','output','error'):
        text=row.get(field)
        if part!='messages' and field in result and not isinstance(text,str):
            result.pop(field, None)
        if isinstance(text,str) and len(text)>limit:
            result[field]=text[:limit]
            result[field+'Detail']={'sessionId':session_id,'part':part,'id':row.get('id'),'field':field,'digest':digest(text),'length':len(text)}
    return result



def work_segments(session):
    """Summaries cover the whole source view, even when action rows are paged."""
    messages=session.get('messages',[])
    timed=sorted((row['createdAt'],index) for index,row in enumerate(messages)
                 if isinstance(row.get('createdAt'),(int,float)) and row.get('timestampKnown') is not False)
    times=[at for at,_ in timed];latest=[];position=-1
    for _,index in timed:
        position=max(position,index);latest.append(position)
    turns={row['id']:row for row in session.get('execution',{}).get('turns',[])}
    groups={};anchors={}
    for node in session.get('execution',{}).get('nodes',[]):
        turn=turns.get(node.get('turnId'),{})
        anchor=turn.get('anchorMessageId')
        at=next((node[key] for key in ('startedAt','endedAt') if isinstance(node.get(key),(int,float))),turn.get('startedAt'))
        if 'anchorMessageId' in node:
            anchor=node['anchorMessageId']
        elif isinstance(at,(int,float)):
            index=bisect_right(times,at)-1
            if index>=0:anchor=messages[latest[index]]['id']
        anchors[node['id']]=anchor
        identity=str(node.get('turnId'))+'@'+(anchor or 'start')
        group=groups.setdefault(identity,{'id':identity,'anchorMessageId':anchor,'turnId':node.get('turnId'),'members':[]})
        group['members'].append(node)
    result=[]
    for group in groups.values():
        nodes=group.pop('members');turn=turns.get(group['turnId'],{})
        starts=[row['startedAt'] for row in nodes if isinstance(row.get('startedAt'),(int,float))]
        ends=[row['endedAt'] for row in nodes if isinstance(row.get('endedAt'),(int,float))]
        running=any(row.get('status',row.get('phase')) in LIVE_PHASES and not row.get('endedAt') for row in nodes)
        running=running or (not starts and not ends and turn.get('phase') in LIVE_PHASES and not turn.get('endedAt'))
        calls=[row for row in nodes if row.get('kind')=='llm']
        failure=next((row.get('status') or row.get('phase') for row in nodes if (row.get('status') or row.get('phase')) in {'error','failed','cancelled','interrupted'}),None)
        group.update(startedAt=min(starts) if starts else turn.get('startedAt'),
                     endedAt=None if running else max(ends) if ends else turn.get('endedAt'),phase='running' if running else failure or ('completed' if ends or turn.get('endedAt') else 'recorded'),
                     nodeCounts={'tools':sum(row.get('kind')=='tool' for row in nodes),'models':sum(row.get('kind')=='llm' for row in nodes)},
                     aggregateUsage=rollup(calls) if calls else None)
        result.append(group)
    return anchors,result

def page(session, part, before=None):
    if part not in {'messages','nodes'}:raise ValueError('Choose messages or nodes.')
    rows=session.get('messages',[]) if part=='messages' else session.get('execution',{}).get('nodes',[])
    end=len(rows)
    if before is not None:
        end=next((i for i,row in enumerate(rows) if row.get('id')==before),None)
        if end is None:raise ValueError('This history changed. Return to the latest messages and try again.')
    start=max(0,end-(MESSAGE_LIMIT if part=='messages' else NODE_LIMIT))
    from .message_interactions import annotate
    items=[compact(annotate(session, row) if part == 'messages' else row,session['id'],part,TEXT_LIMIT if part=='messages' else SUMMARY_LIMIT) for row in rows[start:end]]
    result={'items':items,'offset':start,'total':len(rows),'before':items[0]['id'] if start and items else None}
    if part=='messages':
        result['userOffset']=session.get('sharedHistoryUserTurnOffset',0)+sum(row.get('role')=='user' for row in rows[:start])
    else:
        anchors,segments=work_segments(session)
        for item in items:item['anchorMessageId']=anchors.get(item['id'])
        turn_ids={row.get('turnId') for row in items}
        group_ids={str(row.get('turnId'))+'@'+(row.get('anchorMessageId') or 'start') for row in items}
        result['segments']=[group for group in segments if group['id'] in group_ids]
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
        from .message_interactions import annotate
        result['messages']=[compact(annotate(session,row),session['id'],'messages',TEXT_LIMIT) for row in session.get('messages',[])]
        result.pop('messageWindow',None)
        result['sharedHistoryUserTurnOffset']=session.get('sharedHistoryUserTurnOffset',0)
    if 'execution' in session:
        result['execution']={**{key:value for key,value in session['execution'].items() if key != 'retiredUsageNodes'},'nodes':nodes.pop('items'),'turns':nodes.pop('turns'),'segments':nodes.pop('segments')}
        result['executionWindow']=nodes
    # Reports and completed generation bodies are not activity badges.
    result['workers']=[compact(row,session['id'],'workers',SUMMARY_LIMIT) for row in session.get('workers',[])]
    result['generations']=[{k:v for k,v in row.items() if k!='text'} for row in session.get('generations',[])[-20:]]
    result.pop('messageQuotes', None)
    if 'historyActivity' in result:
        result['historyActivity']={'diagnostics':result['historyActivity'].get('diagnostics',[])}
    return result


def read_text(session, args):
    part=args.get('part');field=args.get('field')
    if part not in {'messages','nodes','workers'} or field not in {'text','summary','detail','report','result','input','output','error','request'}:
        raise ValueError('Choose a valid detail field.')
    rows=session.get('execution',{}).get('nodes',[]) if part=='nodes' else session.get(part,[])
    row=next((row for row in rows if row.get('id')==args.get('id')),None)
    reference = row.get('_eventFields', {}).get(field) if row else None
    if reference:
        from .event_log_view import read_field
        text = read_field(reference)
    else:
        text=row.get(field) if row else None
    if not isinstance(text,str):raise ValueError('This detail is no longer available.')
    if digest(text)!=args.get('digest'):raise ValueError('This detail changed. Refresh this conversation to load the current version.')
    offset=int(args.get('offset',0))
    if offset<0 or offset>len(text):raise ValueError('Invalid detail offset.')
    # An expanded tool reads its source record once, rather than reparsing a
    # large JSON event for every 16 KB response page. Message paging is unchanged.
    end=len(text) if reference and args.get('complete') == 'true' else min(len(text),offset+16000)
    return {'value':text[offset:end],'nextOffset':end if end<len(text) else None,'total':len(text)}
