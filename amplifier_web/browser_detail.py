"""Bounded browser views. Full history remains in the existing session store."""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256

from .execution import LIVE_PHASES, rollup, generation_turns

MESSAGE_LIMIT=60
NATIVE_MESSAGE_LIMIT=100
NODE_LIMIT=100
TEXT_LIMIT=4096
SUMMARY_LIMIT=512


def digest(text):return sha256(text.encode()).hexdigest()

def compact(row, session_id, part, limit):
    if part == 'messages':
        from .peer_attribution import neutral
        row = neutral(row)
    fields = {'anchorMessageId','id','parentId','turnId','sessionId','rootSessionId','kind','phase','status','label','tool','toolCallId','workerId','callId','call_id','provider','model','startedAt','endedAt','updatedAt','createdAt','usage','aggregateUsage','summary','detail','name','agent','report','result','persistent','event','parentSessionId','retryAttempt','retryMax','input','output','error','lifecycle','requestInfo','delegate'}
    fields.update({'exchangeOwnerId', 'toolObservationId', 'savedObservations', 'toolDetail', 'inputSummary', 'outcomeSummary', 'inputPreview', 'actionPath', 'contentAvailability', 'routing', 'runId', 'parentProvider', 'requestCapture', 'responseCapture', 'requestRecording', 'responseRecording', 'exchange', 'parentWorkerId', 'ancestors'})
    result = {key:value for key,value in row.items() if part=='messages' or key in fields}
    if part != 'messages':
        from .tool_observations import metadata as tool_metadata
        result.update(tool_metadata(row))
    if 'routing' in result:
        from .host.model_selection import public_routing
        result['routing'] = public_routing(result['routing'])
    for field in ('input', 'output', 'error', 'request', 'response'):
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



def detail_version(value):
    """Process-local cache validator, not a durable ID or security digest.

    Python caches string hashes: unchanged large inline results are not encoded
    or hashed again on every publication. No result bodies enter the wire stamp.
    """
    if isinstance(value, dict):
        return hash(tuple((key, detail_version(item)) for key, item in sorted(value.items())))
    if isinstance(value, (list, tuple)):
        return hash(tuple(detail_version(item) for item in value))
    return hash(value)


def owned_nodes(session):
    """Carry a small delegate header with each paged action, without its body.

    Workers can start before the current message or detail page. Their child
    calls must still identify their owner, independently of pagination.
    """
    from .delegate_exchange import descriptor, tool_name, delivered_nodes
    original = session.get('execution', {}).get('nodes', [])
    nodes = [*original, *delivered_nodes(session, original)]
    delivered_owners = {row.get('parentId') for row in nodes if row.get('kind') == 'exchange'}
    by_id = {row['id']: row for row in nodes}
    workers_by_id = {row['id']: row for row in nodes if row.get('kind') == 'worker'}
    sessions = {}
    for worker in workers_by_id.values():
        sessions.setdefault(worker.get('sessionId'), []).append(worker)
    # Ambiguous legacy ownership must not assign an old call to the latest run.
    workers = {sid: rows[0] for sid, rows in sessions.items() if sid and len(rows) == 1}
    fields = ('id', 'sessionId', 'label', 'provider', 'model', 'phase', 'status',
              'routing', 'parentProvider', 'runId', 'workerId', 'parentId', 'toolCallId')
    descriptors = {}
    def exchange_for(tool):
        if not tool:
            return None
        if tool['id'] not in descriptors:
            descriptors[tool['id']] = descriptor(tool, session['id'])
        return descriptors[tool['id']]
    headers = {}
    def header(worker, seen=()):
        if worker['id'] in headers:
            return headers[worker['id']]
        owner = {key: worker[key] for key in fields if key in worker}
        parent = by_id.get(worker.get('parentId'))
        exchange = exchange_for(parent) if parent and tool_name(parent) == 'delegate' else None
        if exchange and worker['id'] in delivered_owners:
            exchange = {key: value for key, value in exchange.items() if key not in {'delegateResponse', 'delegateResponseDetail'}}
            exchange['delivery'] = 'separate'
        if exchange:
            owner['exchange'] = exchange
        ancestor = (workers_by_id.get(parent.get('parentId')) or workers.get(parent.get('sessionId'))) if parent else None
        if ancestor and ancestor['id'] not in (*seen, worker['id']) and len(seen) < 20:
            prior = header(ancestor, (*seen, worker['id']))
            owner['parentWorkerId'] = ancestor['id']
            owner['ancestors'] = [*prior.get('ancestors', []), {k: v for k, v in prior.items() if k != 'ancestors'}]
        headers[worker['id']] = owner
        return owner
    try:
        result = []
        for row in nodes:
            worker = workers_by_id.get(row.get('exchangeOwnerId') or row.get('parentId')) or workers.get(row.get('sessionId'))
            if worker and row.get('kind') != 'worker':
                result.append({**row, 'delegate': header(worker),
                               **({'exchange': exchange_for(row)} if row.get('exchangeOwnerId') else {})})
            elif row.get('kind') == 'worker':
                result.append({**row, **header(row)})
            elif row.get('kind') == 'tool' and tool_name(row) == 'delegate':
                # Requests that fail before spawning a worker still have an exchange.
                result.append({**row, 'exchange': exchange_for(row)})
            else:
                result.append(row)
        return result
    finally:
        # The recursive closure otherwise retains session until cyclic GC,
        # preventing prompt retirement of an unborrowed history owner.
        header = None



def work_segments(session, nodes=None, *, select_groups=None):
    """Summaries cover the whole source view, even when action rows are paged."""
    messages=session.get('messages',[])
    timed=sorted((row['createdAt'],index) for index,row in enumerate(messages)
                 if isinstance(row.get('createdAt'),(int,float)) and row.get('timestampKnown') is not False)
    times=[at for at,_ in timed];latest=[];position=-1
    for _,index in timed:
        position=max(position,index);latest.append(position)
    turns={row['id']:row for row in generation_turns(session)}
    groups={};anchors={};last_groups={}
    for node in owned_nodes(session) if nodes is None else nodes:
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
        if node.get('kind') != 'exchange' and (node.get('turnId') not in last_groups or order >= last_groups[node.get('turnId')][0]):
            last_groups[node.get('turnId')]=(order,identity)
    # Group membership is metadata; only summarize/hash the requested page.
    # A compact view must not recursively visit every old tool payload merely
    # to throw all but its last hundred group summaries away afterward.
    positions = {row['id']: index for index, row in enumerate(messages) if row.get('id')}
    def order(group):
        starts = [row['startedAt'] for row in group['members']
                  if isinstance(row.get('startedAt'), (int, float))]
        started = min(starts) if starts else (None if all(row.get('kind') == 'exchange' for row in group['members'])
                                             else turns.get(group['turnId'], {}).get('startedAt'))
        return (positions.get(group['anchorMessageId'], -1),
                started if isinstance(started, (int, float)) else float('-inf'))
    ordered = sorted(groups.values(), key=order)
    result=[]
    for group in select_groups(ordered) if select_groups else ordered:
        nodes=group.pop('members');turn=turns.get(group['turnId'],{})
        starts=[row['startedAt'] for row in nodes if isinstance(row.get('startedAt'),(int,float))]
        ends=[row['endedAt'] for row in nodes if isinstance(row.get('endedAt'),(int,float))]
        running=any(row.get('status',row.get('phase')) in LIVE_PHASES and not row.get('endedAt') for row in nodes)
        last_group=last_groups.get(group['turnId'], (None, None))[1]==group['id']
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
        if last_group and turn_phase in {'error','failed','cancelled','interrupted','stopped'}:
            failure=turn_phase
            running=False
            if isinstance(turn.get('endedAt'),(int,float)):ends.append(turn['endedAt'])
        group.update(startedAt=min(starts) if starts else turn.get('startedAt'),
                     endedAt=None if running else max(ends) if ends else turn.get('endedAt'),phase='running' if running else failure or ('completed' if turn_phase and (ends or turn.get('endedAt')) else 'recorded'),
                     nodeCounts={'tools':sum(row.get('kind')=='tool' for row in nodes),'models':sum(row.get('kind')=='llm' for row in nodes)},
                     aggregateUsage=rollup(calls) if calls else None)
        group['detailRevision']=str(detail_version(nodes))
        if all(row.get('kind') == 'exchange' for row in nodes):
            # Returned reports keep their delivery position but do not inherit
            # the duration or failure of an earlier manager/worker execution.
            group.update(startedAt=min(starts) if starts else None,
                         endedAt=max(ends) if ends else None, phase='completed')
        result.append(group)
    return anchors,result

def page(session, part, before=None, group=None, revision=None, *, resolver=None, window_message_id=None):
    if part == 'nodes' and group is not None:
        from .conversation_navigation import group_source
        session = group_source(session, window_message_id)
    if part == 'groups':
        bounds = {}
        def select(segments):
            end = len(segments)
            if before is not None:
                end = next((i for i, row in enumerate(segments) if row['id'] == before), None)
                if end is None:raise ValueError('This history changed. Refresh this conversation.')
            bounds.update(start=max(0, end - NODE_LIMIT), total=len(segments))
            return segments[bounds['start']:end]
        _, items = work_segments(session, select_groups=select)
        start = bounds['start']
        ids = {row['turnId'] for row in items}
        source_turns = generation_turns(session)
        active = [row['id'] for row in source_turns if row.get('phase') in LIVE_PHASES and not row.get('endedAt')][-20:]
        turns = [row for row in source_turns if row['id'] in ids or row['id'] in active]
        known = {row['id'] for row in turns}
        turns.extend({'id': identity} for identity in dict.fromkeys(row['turnId'] for row in items) if identity not in known)
        return deepcopy({'items': items, 'turns': turns, 'offset': start,
                         'total': bounds['total'], 'before': items[0]['id'] if start and items else None})
    if part not in {'messages','nodes','workers'}:raise ValueError('Choose messages, nodes, workers or groups.')
    rows=owned_nodes(session) if part=='nodes' else session.get(part,[])
    source_nodes = rows if part == 'nodes' else None
    segments_view = work_segments(session, source_nodes,
        select_groups=(lambda rows: [row for row in rows if row['id'] == group]) if group is not None else None) if part == 'nodes' else None
    group_revision = None
    if group is not None:
        if part != 'nodes':raise ValueError('Work details require nodes.')
        anchors, segments = segments_view
        segment = next((row for row in segments if row['id'] == group), None)
        if segment is None:raise ValueError('This work is no longer available. Refresh this conversation.')
        group_revision = segment['detailRevision']
        if revision is not None and revision != group_revision:
            raise ValueError('This work changed. Reload its latest steps.')
        rows = [row for row in rows if row['id'] in anchors and
                str(row.get('turnId'))+'@'+(anchors[row['id']] or 'start') == group]
    end=len(rows)
    if before is not None:
        end=next((i for i,row in enumerate(rows) if row.get('id')==before),None)
        if end is None:raise ValueError('This history changed. Return to the latest messages and try again.')
    message_limit=NATIVE_MESSAGE_LIMIT if session.get('nativeProject') and session.get('historyManaged') else MESSAGE_LIMIT
    start=max(0,end-(message_limit if part=='messages' else NODE_LIMIT))
    from .message_interactions import annotate
    from .peer_attribution import display_annotations
    annotation_source = {**session, 'messageAnnotations': display_annotations(session)} if part == 'messages' else session
    selected = rows[start:end]
    if part == 'messages':
        from .peer_attribution import derive
        selected = derive(session, selected, resolver)
    items=[]
    for row in selected:
        item=compact(annotate(annotation_source, row) if part == 'messages' else row,session['id'],part,TEXT_LIMIT if part=='messages' else SUMMARY_LIMIT)
        if part == 'messages' and row.get('attribution'):
            item['attribution'] = row['attribution']
        items.append(item)
    if part == 'messages':
        enrich_recovery(session, items)
    result={'items':items,'offset':start,'total':len(rows),'before':items[0]['id'] if start and items else None}
    if part=='messages':
        result['userOffset']=session.get('sharedHistoryUserTurnOffset',0)+sum(row.get('role')=='user' for row in rows[:start])
        result['sourceOffset']=(session.get('sharedHistoryOffset',0) if session.get('nativeProject') and session.get('historyManaged') else 0)+start
    elif part == 'nodes':
        anchors,segments=segments_view
        for item in items:item['anchorMessageId']=anchors.get(item['id'])
        turn_ids={row.get('turnId') for row in items}
        group_ids={str(row.get('turnId'))+'@'+(row.get('anchorMessageId') or 'start') for row in items}
        result['segments']=[group for group in segments if group['id'] in group_ids]
        # Keep newly started work visible before its first tool/provider event.
        active=[row['id'] for row in generation_turns(session) if row.get('phase') in LIVE_PHASES and not row.get('endedAt')]
        turn_ids.update(active[-20:])
        counts={identity:{'tools':0,'workers':0} for identity in turn_ids}
        for node in rows:
            if node.get('turnId') in counts and node.get('kind') in {'tool','worker'}:
                counts[node['turnId']]['tools' if node['kind']=='tool' else 'workers']+=1
        result['turns']=[{**row,'nodeCounts':counts[row['id']]} for row in generation_turns(session) if row['id'] in turn_ids]
    if group is not None:
        versions = {row['id']:str(detail_version(row)) for row in rows[start:end]}
        for item in items:item['detailVersion'] = versions[item['id']]
        result.update(group=group, revision=group_revision)
    return deepcopy(result)


def enrich_recovery(session, messages):
    from .browser_workers import recovery_workers
    recovery_workers(session, messages)
    if not any(row.get('observation', {}).get('source') == 'local-job-recovery' for row in messages):
        return
    by_call = {node.get('toolCallId'): node for node in session.get('execution', {}).get('nodes', []) if node.get('kind') == 'tool'}
    for row in messages:
        observation = row.get('observation', {})
        if observation.get('source') != 'local-job-recovery':
            continue
        call = observation.get('recovery', {}).get('call_id') or observation.get('call_id')
        node = by_call.get(call) if call else None
        if node:
            reference = node.get('outputDetail')
            if reference is None and isinstance(node.get('output'), str):
                reference = {'sessionId': session['id'], 'part': 'nodes', 'id': node['id'],
                    'field': 'output', 'digest': digest(node['output']), 'length': len(node['output'])}
            row['recoveryResult'] = {'id': node['id'], 'label': node.get('label'), 'outputDetail': reference}


def sync_work(session, group, known, *, window_message_id=None):
    """Refresh an open window with changed rows only; retain no client cache."""
    if not isinstance(known, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in known.items()):
        raise ValueError('Work versions must map step IDs to version strings.')
    from .conversation_navigation import group_source
    session = group_source(session, window_message_id)
    source_nodes = owned_nodes(session)
    anchors, segments = work_segments(session, source_nodes,
        select_groups=lambda rows: [row for row in rows if row['id'] == group])
    segment = next((row for row in segments if row['id'] == group), None)
    if segment is None:raise ValueError('This work is no longer available. Refresh this conversation.')
    rows = [row for row in source_nodes
            if row['id'] in anchors and str(row.get('turnId'))+'@'+(anchors[row['id']] or 'start') == group]
    # Keep earlier pages the user opened, including long-running delegations.
    # If an archive was replaced, start with a fresh bounded page.
    start = next((i for i, row in enumerate(rows) if row['id'] in known), max(0, len(rows)-NODE_LIMIT))
    items=[]
    for row in rows[start:]:
        version=str(detail_version(row))
        if known.get(row['id']) == version:continue
        item=compact(row,session['id'],'nodes',SUMMARY_LIMIT)
        item.update(anchorMessageId=anchors.get(row['id']), detailVersion=version)
        items.append(item)
    return deepcopy({'items':items, 'order':[row['id'] for row in rows[start:]],
                     'offset':start, 'total':len(rows), 'before':rows[start]['id'] if start and rows else None,
                     'group':group, 'revision':segment['detailRevision']})


def project(session, *, resolver=None, message_overlay=None, work_overlay=None):
    if work_overlay is not None:
        from .cold_display import raw
        # ColdRecord's ordinary mapping interface materializes all virtual
        # bodies. A requested display window has no need to issue those aliases.
        resident = raw(session)
        session = {key: value for key, value in resident.items()
                   if not key.startswith('_cold') and key not in {'execution', 'historyActivity'}}
        session['messages'] = (message_overlay or {}).get('messages', resident.get('messages', [])[-100:])
        session['execution'] = {'nodes': [], 'turns': []}
        visible = {row['id'] for row in session['messages']}
        session['messageAnnotations'] = {key: value for key, value in resident.get('messageAnnotations', {}).items() if key in visible}
    result=dict(session)
    from .peer_attribution import display_annotations
    result['messageAnnotations'] = display_annotations(session)
    if message_overlay is not None:
        result.update(message_overlay)
        messages = None
    else:
        messages = page(session,'messages',resolver=resolver)
    groups = page(session,'groups') if work_overlay is None else None
    if messages is not None:
        result.update(messages=messages.pop('items'),messageWindow=messages,
                      sharedHistoryUserTurnOffset=messages['userOffset'])
    # Native disk paging can retain thousands of previously loaded messages.
    # Browser projection stays bounded independently; older rows remain available
    # through the same message cursor before fetching another disk page.
    if work_overlay is not None:
        result.update(work_overlay)
    elif 'execution' in session:
        result['execution']={**{key:value for key,value in session['execution'].items() if key != 'retiredUsageNodes'},'nodes':[],'turns':groups.pop('turns'),'segments':groups.pop('items'),'detailsDeferred':True}
        from .image_generation import project as image_work
        result['execution']['imageGeneration'] = image_work(session, session['execution'])
        result['executionWindow']={**groups,'part':'groups'}
    # Reports and completed generation bodies are not activity badges.
    from .browser_workers import project as worker_view
    workers, summary = worker_view(session.get('workers', []), result.get('execution', {}))
    result['workers']=[compact(row,session['id'],'workers',SUMMARY_LIMIT) for row in workers]
    if summary is not None:
        result['workerSummary'] = summary
    result['generations']=[{k:v for k,v in row.items() if k!='text'} for row in session.get('generations',[])[-20:]]
    result.pop('messageQuotes', None)
    if 'historyActivity' in result:
        result['historyActivity']={'diagnostics':result['historyActivity'].get('diagnostics',[])}
    return result


def read_text(session, args):
    part=args.get('part');field=args.get('field')
    if part not in {'messages','nodes','workers'} or field not in {'text','summary','detail','report','result','input','output','error','request','response','delegateRequest','delegateResponse'}:
        raise ValueError('Choose a valid detail field.')
    rows=session.get('execution',{}).get('nodes',[]) if part=='nodes' else session.get(part,[])
    row=next((row for row in rows if row.get('id')==args.get('id')),None)
    if row is None and part == 'messages' and session.get('nativeProject') and session.get('historyManaged'):
        from .conversation_navigation import source
        facts, read = source(session)
        position = next((i for i, fact in enumerate(facts) if fact[0] == args.get('id')), None)
        if position is not None:
            row = read([position])[0]
    reference = row.get('_eventFields', {}).get(field) if row else None
    if reference:
        from .event_log_view import read_field
        text = read_field(reference)
    else:
        text=row.get(field) if row else None
        if row and field in {'delegateRequest', 'delegateResponse'}:
            from .delegate_exchange import field_value, observation_response
            text = observation_response(row) if part == 'messages' and field == 'delegateResponse' else field_value(row, field)
    if not isinstance(text,str):raise ValueError('This detail is no longer available.')
    from .event_log_view import event_field_digest
    detail = row.get(field + 'Detail', {}) if row else {}
    expected = event_field_digest(reference) if reference and detail.get('digestKind') == 'event-field-v1' else digest(text)
    if expected != args.get('digest') and digest(text) != args.get('digest'):raise ValueError('This detail changed. Refresh this conversation to load the current version.')
    offset=int(args.get('offset',0))
    if offset<0 or offset>len(text):raise ValueError('Invalid detail offset.')
    # An expanded tool reads its source record once, rather than reparsing a
    # large JSON event for every 16 KB response page. Message paging is unchanged.
    end=len(text) if reference and args.get('complete') == 'true' else min(len(text),offset+16000)
    return {'value':text[offset:end],'nextOffset':end if end<len(text) else None,'total':len(text)}
