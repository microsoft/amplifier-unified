"""Deduplicated public execution tree and usage rollups (no reasoning payloads)."""
import time

USAGE_KEYS=('inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens','totalTokens')

def ensure_turn(session,identity,label=''):
    tree=session.setdefault('execution',{'nodes':[],'turns':[],'currentTurnId':None})
    if identity and not any(t['id']==identity for t in tree['turns']):
        tree['turns'].append({'id':identity,'inputId':identity,'label':label[:100],'startedAt':time.time(),'phase':'running','aggregateUsage':rollup([])})
    anchor_turns(session)
    if identity:tree['currentTurnId']=identity
    return tree


def anchor_turns(session):
    """Pin work where it began, including voice work without a visible input ID.

    A null anchor means before the first message. Never move a saved anchor when
    a later transcript, response, completion, or worker update arrives.
    """
    messages=[m for m in session.get('messages',[]) if m.get('id')]
    for turn in session.get('execution',{}).get('turns',[]):
        if 'anchorMessageId' in turn:
            continue
        exact=next((m for m in messages if m.get('role')=='user' and (
            m['id'] in {turn.get('messageId'),turn.get('userMessageId')} or
            (m.get('inputId') and m['inputId'] in {turn.get('inputId'),turn.get('id')}))),None)
        started=turn.get('startedAt')
        preceding=[m for m in messages if isinstance(started,(int,float)) and
                   isinstance(m.get('createdAt'),(int,float)) and m['createdAt']<=started]
        anchor=exact or (preceding[-1] if preceding else None)
        turn['anchorMessageId']=anchor['id'] if anchor else None


def rollup(calls):
    result={key:0 for key in USAGE_KEYS}
    result.update(calls=len(calls),costUsd=0.0,pricedCalls=0,estimatedCalls=0,unknownCalls=0,tokenUnknownCalls=0)
    for node in calls:
        usage=node.get('usage') or {}
        for key in USAGE_KEYS:
            value=usage.get(key)
            if isinstance(value,(int,float)) and value>=0:result[key]+=value
        if not usage or not any(k in usage for k in ('inputTokens','outputTokens','totalTokens')):result['tokenUnknownCalls']+=1
        cost=usage.get('costUsd')
        if isinstance(cost,(int,float)) and cost>=0:
            result['costUsd']+=cost;result['pricedCalls']+=1
            result['estimatedCalls']+=int(usage.get('costType')=='estimated')
        else:result['unknownCalls']+=1
    result['costType']='unavailable' if not result['pricedCalls'] else 'partial' if result['unknownCalls'] else 'estimated' if result['estimatedCalls'] else 'reported'
    return result


def ingest(session,event):
    tree=ensure_turn(session,None)
    identity=event.get('id')
    if not identity:return
    allowed={'id','parentId','turnId','sessionId','rootSessionId','kind','phase','label','toolCallId','provider','model','startedAt','endedAt','usage','summary'}
    safe={k:v for k,v in event.items() if k in allowed}
    node=next((n for n in tree['nodes'] if n['id']==identity),None)
    if node:node.update(safe)
    else:
        node=safe;tree['nodes'].append(node)
    lookup={n['id']:n for n in tree['nodes']}
    for candidate in tree['nodes']:
        if not candidate.get('turnId'):
            parent=lookup.get(candidate.get('parentId'),{})
            candidate['turnId']=parent.get('turnId') or tree.get('currentTurnId')
    # Usage is counted once per LLM call. Tool/worker summaries never add their
    # own accumulated totals, preventing double counting at every ancestor.
    calls=[n for n in tree['nodes'] if n.get('kind')=='llm']
    descendants={n['id']:[] for n in tree['nodes']}
    for call in calls:
        cursor=call;seen=set()
        while cursor and cursor['id'] not in seen:
            seen.add(cursor['id']);descendants[cursor['id']].append(call)
            cursor=lookup.get(cursor.get('parentId'))
    for candidate in tree['nodes']:candidate['aggregateUsage']=rollup(descendants[candidate['id']])
    for turn in tree['turns']:turn['aggregateUsage']=rollup([c for c in calls if c.get('turnId')==turn['id']])
    tree['aggregateUsage']=rollup(calls)


def finish(session,status='completed'):
    tree=session.get('execution',{})
    for turn in tree.get('turns',[]):
        if turn['phase']=='running':turn.update(phase=status,endedAt=time.time())
