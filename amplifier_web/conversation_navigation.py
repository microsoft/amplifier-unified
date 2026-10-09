"""Passive navigation over canonical history; message bodies are read on demand."""
from hashlib import sha256

from .browser_detail import MESSAGE_LIMIT, TEXT_LIMIT, compact, work_segments


def source(session):
    if session.get('nativeProject') and session.get('historyManaged'):
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session.jsonl import TranscriptIndex
        from .automatic_history import directory, display_message
        rows = SessionHistoryStore(directory(session)).indexed_messages()
        def fact(value, index):
            row = display_message(value, index, session)
            return (row['id'], row['role'], not bool(row.get('observation')), index) if row else None
        facts = rows.project('unified-navigation-v1:'+session['id'], fact) if isinstance(rows, TranscriptIndex) else [fact(row,i) for i,row in enumerate(rows)]
        visible = [row for row in facts if row]
        def read(positions):
            indices = [visible[position][3] for position in positions]
            bodies = rows.read_positions(indices) if isinstance(rows, TranscriptIndex) else [rows[index] for index in indices]
            return [display_message(body,index,session) for index,body in zip(indices,bodies)]
        return visible, read
    rows = session.get('messages', [])
    return [(row['id'], row.get('role'), not bool(row.get('observation')), i) for i,row in enumerate(rows)], lambda positions:[rows[i] for i in positions]


def query(session, *, message_id=None, window=False):
    facts, read = source(session)
    revision = sha256('\n'.join(row[0] for row in facts).encode()).hexdigest()
    if message_id is None:
        # Only IDs and ordinals travel here, never transcript or tool text.
        return {'revision':revision, 'turns':[{'id':row[0],'position':row[3]} for row in facts if row[1]=='user' and row[2]], 'totalMessages':len(facts)}
    position = next((i for i,row in enumerate(facts) if row[0]==message_id), None)
    if position is None:
        raise ValueError('This message is no longer available. Refresh the chat navigator.')
    if not window:
        stop = next((i for i in range(position+1,len(facts)) if facts[i][1]=='user' and facts[i][2]), len(facts))
        reply = next((i for i in range(position+1,stop) if facts[i][1]=='assistant' and facts[i][2]), None)
        bodies = read([position]+([reply] if reply is not None else []))
        return {'id':message_id,'revision':revision,'text':bodies[0].get('text','')[:180], 'reply':bodies[1].get('text','')[:280] if len(bodies)>1 else ''}
    start = max(0,position-5);end=min(len(facts),start+MESSAGE_LIMIT)
    from .message_interactions import annotate
    messages=[compact(annotate(session,row),session['id'],'messages',TEXT_LIMIT) for row in read(range(start,end))]
    anchors={row['id'] for row in messages}
    _,segments=work_segments(session)
    segments=[row for row in segments if row.get('anchorMessageId') in anchors]
    turn_ids={row['turnId'] for row in segments}
    return {'revision':revision,'messages':messages,'offset':start,'sourceOffset':facts[start][3],'total':len(facts),
            'before':facts[max(0,start-(MESSAGE_LIMIT-5))][0] if start else None,'after':facts[end][0] if end<len(facts) else None,
            'userOffset':sum(row[1]=='user' and row[2] for row in facts[:start]),
            'execution':{'nodes':[],'segments':segments,'turns':[row for row in session.get('execution',{}).get('turns',[]) if row['id'] in turn_ids],'detailsDeferred':True}}
