"""Bounded browser views. Full history remains in the existing session store."""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256

from .execution import LIVE_PHASES, rollup
from .provider_wait import public_wait

MESSAGE_LIMIT=60
NODE_LIMIT=100
TEXT_LIMIT=4096
SUMMARY_LIMIT=512


def digest(text):return sha256(text.encode()).hexdigest()

def _public_failure(value):
    """Keep fixed session-health contracts, not plugin messages or payloads."""
    if not isinstance(value, dict):
        return None
    from .session_health import failure_details, generation_failure
    category = value.get('category')
    if not isinstance(category, str):
        return None
    # These are trusted selectors for existing fixed copy, never incoming error
    # text. Matching the whole bounded contract avoids reclassifying a failure.
    hints = {'provider_selection': 'ProviderSelectionError:',
             'invalid_image': 'InvalidImageError:', 'context_limit': 'contextlengtherror',
             'tool_configuration': 'ToolConfigurationError:',
             'authentication': 'authentication', 'rate_limit': 'rate limit'}
    contracts = [failure_details(hints.get(category, ''), value.get('errorType')),
                 generation_failure({'error_category': category,
                                     'error_type': value.get('errorType'),
                                     'error_stage': value.get('stage'),
                                     'retryable': value.get('retryable')})]
    if category in {'provider_timeout', 'provider_outcome_unknown'}:
        contract = failure_details({'retryable': False, 'request_outcome': 'unknown',
                                    'effects': 'may_have_occurred'})
        if category == 'provider_timeout':
            contract.update(category=category, errorType='LLMTimeoutError')
        contracts.append(contract)
    if category == 'computer_capture_stop':
        contracts.append(failure_details({'code': 'computer_result_not_image',
                                         'result_kind': value.get('resultKind')}))
    for contract in contracts:
        if contract and all(key in value and type(value[key]) is type(expected)
                            and value[key] == expected for key, expected in contract.items()):
            return contract
    return None

def compact(row, session_id, part, limit):
    fields = {'anchorMessageId','id','parentId','turnId','sessionId','rootSessionId','kind','phase','status','label','tool','toolCallId','workerId','callId','call_id','provider','model','startedAt','endedAt','updatedAt','createdAt','usage','aggregateUsage','summary','detail','name','agent','report','result','persistent','event','parentSessionId','retryAttempt','retryMax','input','output','error','lifecycle','requestInfo'}
    fields.update({'routing', 'runId', 'parentProvider', 'requestCapture'})
    result = {key:value for key,value in row.items() if part=='messages' or key in fields}
    if part == 'nodes' and row.get('kind') == 'llm':
        wait = public_wait(row.get('providerWait'))
        if wait is not None:
            result['providerWait'] = wait
    if part == 'nodes':
        failure = _public_failure(row.get('failure'))
        if failure is not None:
            result['failure'] = failure
    if 'routing' in result:
        from .host.model_selection import public_routing
        result['routing'] = public_routing(result['routing'])
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
    groups={};anchors={};last_groups={}
    for node in session.get('execution',{}).get('nodes',[]):
        if node.get('lifecycle')=='background' and node.get('label')=='Session naming':continue
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
        order=(at if isinstance(at,(int,float)) else float('-inf'),len(anchors))
        if node.get('turnId') not in last_groups or order >= last_groups[node.get('turnId')][0]:
            last_groups[node.get('turnId')]=(order,identity)
    result=[]
    for group in groups.values():
        nodes=group.pop('members');turn=turns.get(group['turnId'],{})
        starts=[row['startedAt'] for row in nodes if isinstance(row.get('startedAt'),(int,float))]
        ends=[row['endedAt'] for row in nodes if isinstance(row.get('endedAt'),(int,float))]
        running=any(row.get('status',row.get('phase')) in LIVE_PHASES and not row.get('endedAt') for row in nodes)
        last_group=last_groups[group['turnId']][1]==group['id']
        running=running or (last_group and (turn.get('status') or turn.get('phase')) in LIVE_PHASES and not turn.get('endedAt'))
        calls=[row for row in nodes if row.get('kind')=='llm']
        failure=None
        # Failed tools, delegated attempts and recovered provider requests are
        # evidence for the agent, not a failed conversation. Keep their exact
        # outcomes on their rows; only the manager lifecycle fails this summary.
        # Keep the current segment active between calls until that lifecycle
        # settles, rather than flickering after each completed/error action.
        # A manager can fail after its final model call succeeded. Attribute
        # that outcome to the last segment without rewriting earlier work or
        # the successful child call. Use the full tree, including paged nodes.
        turn_phase=turn.get('status') or turn.get('phase')
        if last_group and turn_phase in {'error','failed','cancelled','interrupted'}:
            failure=turn_phase
            running=False
            if isinstance(turn.get('endedAt'),(int,float)):ends.append(turn['endedAt'])
        group.update(startedAt=min(starts) if starts else turn.get('startedAt'),
                     endedAt=None if running else max(ends) if ends else turn.get('endedAt'),phase='running' if running else failure or ('completed' if turn_phase and (ends or turn.get('endedAt')) else 'recorded'),
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
    from .voice_messages import project_message
    items=[compact(project_message(session, annotate(session, row)) if part == 'messages' else row,session['id'],part,TEXT_LIMIT if part=='messages' else SUMMARY_LIMIT) for row in rows[start:end]]
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
        from .voice_messages import project_message
        result['messages']=[compact(project_message(session,annotate(session,row)),session['id'],'messages',TEXT_LIMIT) for row in session.get('messages',[])]
        result.pop('messageWindow',None)
        result['sharedHistoryUserTurnOffset']=session.get('sharedHistoryUserTurnOffset',0)
    if 'execution' in session:
        result['execution']={**{key:value for key,value in session['execution'].items() if key != 'retiredUsageNodes'},'nodes':nodes.pop('items'),'turns':nodes.pop('turns'),'segments':nodes.pop('segments')}
        result['executionWindow']=nodes
    # Reports and completed generation bodies are not activity badges.
    result['workers']=[compact(row,session['id'],'workers',SUMMARY_LIMIT) for row in session.get('workers',[])]
    result['generations']=[{k:v for k,v in row.items() if k!='text'} for row in session.get('generations',[])[-20:]]
    result.pop('messageQuotes', None)
    result.pop('streamingGenerations', None)
    # Changing a verdict invalidates earlier browser pages, not only the tail.
    import json
    result['voicePresentationRevision'] = digest(json.dumps(
        [session.get('voiceResponses', {}), session.get('voiceMembership', {}),
         session.get('voiceCalls', {})], sort_keys=True))
    for field in ('voiceResponses', 'voiceMembership', 'voiceMembershipIncomplete', 'voiceCalls'):
        result.pop(field, None)
    if session.get('streaming'):
        from .voice_messages import project_message
        stream = project_message(session, {'id': session.get('streamingId'), 'role': 'assistant',
            'text': session['streaming'], 'rootGenerations': session.get('streamingGenerations', [])})
        if stream.get('presentation') == 'backend-relay':
            result.pop('streaming', None)
    if 'historyActivity' in result:
        result['historyActivity']={'diagnostics':result['historyActivity'].get('diagnostics',[])}
    return result


def full_project(session):
    """Preserve explicit full-history reads while applying the same policy."""
    from .voice_messages import project_message
    result = project(session)
    result['messages'] = [project_message(session, row) for row in session.get('messages', [])]
    result.pop('messageWindow', None)
    return result


def read_text(session, args):
    part=args.get('part');field=args.get('field')
    if part not in {'messages','nodes','workers'} or field not in {'text','summary','detail','report','result','input','output','error','request'}:
        raise ValueError('Choose a valid detail field.')
    rows=session.get('execution',{}).get('nodes',[]) if part=='nodes' else session.get(part,[])
    row=next((row for row in rows if row.get('id')==args.get('id')),None)
    if row is None and part == 'messages' and session.get('nativeProject'):
        from .automatic_history import read_transcript
        row = next((item for item in read_transcript(session, limit=None)['messages']
                    if item.get('id') == args.get('id')), None)
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
