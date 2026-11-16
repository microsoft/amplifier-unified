"""Bounded display-only transcript selection and fresh currentness witnesses."""
from .suggestion_settings import digest

MAX_ROWS = 100
MAX_BYTES = 24 * 1024


class Unavailable(ValueError):
    pass


def public_rows(rows, *, bounded=True):
    result, size = [], 0
    for row in rows:
        if (row.get('role') not in {'user', 'assistant'} or row.get('partial')
                or row.get('ephemeral') or row.get('private')
                or row.get('visibility') in {'internal', 'developer'}
                or row.get('observation')
                or row.get('delivery', {}).get('status') in {
                    'pending', 'sending', 'failed', 'unknown', 'queued', 'queued_for_update'}
                or row.get('inputStatus', {}).get('phase') in {
                    'queued', 'working', 'waiting', 'failed', 'unknown'}):
            continue
        metadata = row.get('metadata') or {}
        if not isinstance(metadata, dict) or any(metadata.get(key) for key in
                ('ephemeral', 'private', 'amplifier_visible_reference', 'amplifier_recovery_reference')):
            continue
        text = row.get('text')
        if not isinstance(text, str) or not text.strip():
            continue
        # Check chars before encoding: a giant row never allocates a giant copy.
        if bounded and len(text) > MAX_BYTES:
            raise Unavailable('transcript_limit')
        item = {key: row[key] for key in ('id', 'role', 'text', 'agentId', 'agentLabel')
                if isinstance(row.get(key), str)}
        peer = row.get('peerEnvelope')
        if row['role'] == 'user' and isinstance(peer, dict) and isinstance(peer.get('senderSessionId'), str):
            item['agentId'] = peer['senderSessionId']
            item['agentLabel'] = 'Another conversation'
        elif row['role'] == 'user' and row.get('inputOrigin') in {'agent', 'peer', 'scheduler'}:
            item['agentId'] = row.get('inputOrigin')
            item['agentLabel'] = 'Agent or scheduled input'
        if not bounded:
            result.append(item)
            continue
        size += len(text.encode()) + sum(len(item.get(key, '').encode())
                                        for key in ('id', 'role', 'agentId', 'agentLabel'))
        if size > MAX_BYTES or len(result) >= MAX_ROWS:
            raise Unavailable('transcript_limit')
        result.append(item)
    return result


def select(rows, config, has_earlier):
    if config['transcriptMode'] == 'full':
        if has_earlier:
            raise Unavailable('full_transcript_exceeds_bound')
        return rows
    human = [index for index, row in enumerate(rows)
             if row['role'] == 'user' and not row.get('agentId')]
    if len(human) < config['humanTurns'] and has_earlier:
        raise Unavailable('human_turn_window_exceeds_bound')
    return rows[human[max(0, len(human) - config['humanTurns'])]:] if human else rows


def witness(service, sid, config=None):
    """No cold hydration or prefix traversal. Resident witness is <=100 rows."""
    windows = service.history.windows
    session = service._session(sid, hydrate=False)
    from .transcript_windows import _source
    owner = [_source(session), windows._fence(sid), session.get('archived'),
             session.get('_deleting'), session.get('status'), session.get('interruptionRevision')]
    if session.get('historyManaged') is True:
        from .automatic_history import directory
        source = _source(session)
        revision = windows.reader.source_revision(directory(source), {
            'project': source['nativeProject'],
            'nativeId': source.get('nativeIdentity') or source.get('runtimeSessionId') or sid})
        return digest([owner, revision])
    cold = windows._cold_source(session)
    if cold is not None:
        # Immutable resource ownership, plus writer invalidation, not a cached body.
        from .transcript_window import _stamp
        return digest([owner, cold.identity, _stamp(cold.directory / (cold.identity + '.json'))])
    rows = dict.get(session, 'messages', ())
    tail = rows[max(0, len(rows) - MAX_ROWS):]
    projected = public_rows(tail, bounded=False)
    if config is not None:
        projected = select(projected, config, len(rows) > MAX_ROWS)
    return digest([owner, len(rows), public_rows(projected)])


async def capture(service, sid, config, expected_last):
    before = witness(service, sid, config)
    windows = service.history.windows
    page = await windows.read(sid, limit=MAX_ROWS, public_only=True)
    retained = windows.pages.get((sid, page['windowKey']))
    if retained is None or page.get('unavailableRanges'):
        raise Unavailable('transcript_unavailable')
    # Borrow only this bounded, maintained display page. Do not read raw native
    # context or expand textDetail into an unrelated history read.
    from .input_status import annotate
    session = service._session(sid, hydrate=False)
    rows = public_rows([annotate(session, row) if row.get('inputId') or row.get('nativeInputId') else row
                        for row in retained[0]['items']], bounded=False)
    if not rows or rows[-1]['role'] != 'assistant':
        raise Unavailable('no_completed_assistant')
    if rows[-1]['id'] != expected_last:
        raise Unavailable('source_changed')
    selected = public_rows(select(rows, config, page['hasEarlier']))
    if not any(row['role'] == 'user' and not row.get('agentId') for row in selected):
        raise Unavailable('no_human_turn')
    after = witness(service, sid, config)
    if before != after:
        raise Unavailable('source_changed')
    return selected, after