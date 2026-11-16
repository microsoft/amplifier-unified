"""Bounded display-only transcript selection and fresh currentness witnesses."""
import json

from .suggestion_settings import digest

MAX_ROWS = 100
MAX_BYTES = 24 * 1024


class Unavailable(ValueError):
    pass


def speaker(row):
    """Canonical input provenance, never provider role or text-prefix guesses."""
    actor = row.get('inputActor') or {}
    actor = actor if isinstance(actor, dict) else {}
    peer = row.get('peerEnvelope') or {}
    peer = peer if isinstance(peer, dict) else {}
    kind, identity = actor.get('kind'), actor.get('id')
    origin = row.get('inputOrigin')
    if kind in {'agent', 'system', 'unknown'}:
        return {'kind': kind, **({'id': identity} if isinstance(identity, str) else {})}
    if row.get('agentId'):
        return {'kind': 'agent', 'id': row['agentId']}
    if peer.get('senderSessionId'):
        return {'kind': 'agent', 'id': peer['senderSessionId']}
    if origin in {'agent', 'peer', 'scheduler', 'approval', 'observation', 'system'}:
        return {'kind': 'agent' if origin in {'agent', 'peer'} else 'system',
                'id': origin}
    if row.get('scheduledRunId') or row.get('approvalId') or row.get('questionId'):
        return {'kind': 'system'}
    metadata = row.get('metadata') or {}
    marker = metadata.get('amplifier_input') if isinstance(metadata, dict) else None
    if marker is not None:
        if isinstance(marker, dict) and type(marker.get('version')) is int and marker['version'] == 1:
            source = marker.get('source')
            if marker.get('kind') == 'service' or source in {'system', 'scheduler', 'observation'}:
                return {'kind': 'system'}
            if source in {'agent', 'peer'}:
                return {'kind': 'agent'}
            if marker.get('kind') in {'user', 'steer'} and source == 'user' and isinstance(marker.get('id'), str):
                return {'kind': 'human'}
        return {'kind': 'unknown'}
    if row.get('role') == 'assistant':
        return {'kind': 'agent'}
    if kind == 'human' or origin in {'ui', 'user', 'voice'}:
        return {'kind': 'human', **({'id': identity} if isinstance(identity, str) else {})}
    return {'kind': 'unknown'}


def display_inputs(service, session, rows):
    """Native progress and external conversation acceptance are different facts."""
    from .input_status import annotate
    result = []
    external = bool(session.get('externalAgent'))
    for row in rows:
        item = row
        if row.get('role') == 'user':
            if external:
                # ACP/shared human inputs do not have native generation events.
                # Their canonical conversation acceptance is the display proof.
                if (row.get('delivery') or {}).get('status') != 'accepted':
                    continue
                item = {key: value for key, value in row.items() if key != 'inputStatus'}
            elif row.get('inputId') or row.get('nativeInputId'):
                item = annotate(session, row)
            if row.get('source') == 'native' and row.get('nativeInputId'):
                # Native role=user/source=user can also carry a peer delivery.
                # Resolve only its exact local receipt; never scan history or
                # infer a human from a host-looking string in the message body.
                saved = service.db.execute('SELECT receipt FROM commands WHERE id=?',
                                           (row['nativeInputId'],)).fetchone()
                if saved:
                    try:
                        receipt = json.loads(saved[0])
                    except (ValueError, TypeError):
                        receipt = {}
                    if isinstance(receipt, dict) and receipt.get('commandAction') == 'coordination.send':
                        actor = {'kind': 'system' if receipt.get('origin') == 'system' else 'agent'}
                        item = {**item, 'inputActor': actor}
        result.append(item)
    return result


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
        from .voice_input_projection import public_input
        voice = public_input(text, metadata.get('amplifier_input'))
        if voice:
            text = voice['text']
        from .naming_context import clean_text, MAX_TEXT_BYTES
        # Refuse overlong individual text rather than silently using the naming
        # helper's clipped prefix as a complete conversation message.
        if len(text) > MAX_TEXT_BYTES or len(text.encode()) > MAX_TEXT_BYTES:
            raise Unavailable('transcript_limit')
        text = clean_text(text)
        if not text:
            continue
        # Check chars before encoding: a giant row never allocates a giant copy.
        if bounded and len(text) > MAX_BYTES:
            raise Unavailable('transcript_limit')
        item = {key: row[key] for key in ('id', 'role', 'text', 'agentId', 'agentLabel')
                if isinstance(row.get(key), str)}
        item['text'] = text
        item['inputActor'] = speaker(row)
        if row['role'] == 'user' and item['inputActor']['kind'] != 'human':
            item['agentId'] = item['inputActor'].get('id') or item['inputActor']['kind']
            item.setdefault('agentLabel', 'Input from ' + item['inputActor']['kind'])
        if not bounded:
            result.append(item)
            continue
        size += len(text.encode()) + sum(len(item.get(key, '').encode())
                                        for key in ('id', 'role', 'agentId', 'agentLabel'))
        size += sum(len(value.encode()) for value in item['inputActor'].values())
        if size > MAX_BYTES or len(result) >= MAX_ROWS:
            raise Unavailable('transcript_limit')
        result.append(item)
    return result


def eligible(service, session):
    from .session_navigation import is_top_level
    from .updates import work_paused
    return (is_top_level(session) and not session.get('scheduledRunId')
            and not work_paused(getattr(service, '_state', {})))


def select(rows, config, has_earlier):
    if config['transcriptMode'] == 'full':
        if has_earlier:
            raise Unavailable('full_transcript_exceeds_bound')
        return rows
    human = [index for index, row in enumerate(rows)
             if row['role'] == 'user' and speaker(row)['kind'] == 'human']
    if len(human) < config['humanTurns'] and has_earlier:
        raise Unavailable('human_turn_window_exceeds_bound')
    return rows[human[max(0, len(human) - config['humanTurns'])]:] if human else rows


def witness(service, sid, config=None, *, durable=False):
    """No cold hydration or prefix traversal. Resident witness is <=100 rows."""
    windows = service.history.windows
    session = service._session(sid, hydrate=False)
    from .transcript_windows import _source
    owner = ([_source(session)] if durable else [
        _source(session), windows._fence(sid), session.get('archived'),
        session.get('_deleting'), session.get('status'), session.get('interruptionRevision')])
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
    projected = public_rows(display_inputs(service, session, tail), bounded=False)
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
    session = service._session(sid, hydrate=False)
    rows = public_rows(display_inputs(service, session, retained[0]['items']), bounded=False)
    if not rows or rows[-1]['role'] != 'assistant':
        raise Unavailable('no_completed_assistant')
    if rows[-1]['id'] != expected_last:
        raise Unavailable('source_changed')
    selected = public_rows(select(rows, config, page['hasEarlier']))
    if not any(row['role'] == 'user' and speaker(row)['kind'] == 'human' for row in selected):
        raise Unavailable('no_human_turn')
    after = witness(service, sid, config)
    if before != after:
        raise Unavailable('source_changed')
    return selected, after