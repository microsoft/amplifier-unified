"""Bounded display-only transcript selection and fresh currentness witnesses."""
import json

from .suggestion_settings import digest

MAX_ROWS = 100
MAX_BYTES = 24 * 1024
SOURCE_COMPONENTS = (
    'owner_native', 'runtime_session', 'created', 'status', 'stop', 'archive', 'deleting',
    'storage_mode', 'cold_resource', 'cold_stamp', 'rows_count', 'rows_id', 'rows_role',
    'rows_text', 'rows_actor', 'rows_label')
SOURCE_REASONS = frozenset('current_source_' + key for key in SOURCE_COMPONENTS)


def changed_component(before, after):
    """One finite label; neither fingerprints nor caller-controlled keys escape."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in SOURCE_COMPONENTS:
            if before.get(key) != after.get(key):
                return 'current_source_' + key
    return 'current_sourcewitness'


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
    require_settled_tail(rows)
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
    require_settled_tail(result)
    return result


def require_settled_tail(rows):
    """Do not mistake filtered, newer saved input for an unchanged idle tail.

    Inspect only the caller's already bounded suffix. Older failed inputs before
    a completed public assistant are not a permanent hold on later work.
    """
    for row in reversed(rows):
        if (row.get('ephemeral') or row.get('private') or row.get('observation')
                or row.get('visibility') in {'internal', 'developer'}):
            continue
        if row.get('role') == 'assistant' and public_rows([row], bounded=False):
            return
        if row.get('role') == 'user' and (
                (row.get('delivery') or {}).get('status') in {
                    'pending', 'sending', 'failed', 'unknown', 'queued', 'queued_for_update'}
                or (row.get('inputStatus') or {}).get('phase') in {
                    'queued', 'working', 'waiting', 'failed', 'unknown'}):
            raise Unavailable('input_not_settled')


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
    from .update_drain import held
    return (is_top_level(session) and not session.get('scheduledRunId')
            and not work_paused(getattr(service, '_state', {})) and not held(service))


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


def witness(service, sid, config=None, *, components=None):
    """No cold hydration or prefix traversal. Resident witness is <=100 rows."""
    windows = service.history.windows
    session = service._session(sid, hydrate=False)
    from .transcript_windows import _source
    owner = [
        _source(session), session.get('archived'), session.get('_deleting'),
        session.get('status') in {'idle', 'ready'}, session.get('interruptionRevision')]
    def observed(*, projected=None, cold=None, stamp=None):
        # Diagnostics borrow the SAME bounded rows/owner/stamp already used by
        # the total witness. Keep only fixed-size private hashes.
        if components is None:
            return
        source = owner[0]
        values = {
            'owner_native': [source[key] for key in ('id', 'nativeProject', 'nativeIdentity')],
            'runtime_session': source['runtimeSessionId'], 'created': source['createdAt'],
            'archive': owner[1], 'deleting': owner[2], 'status': owner[3], 'stop': owner[4],
            'storage_mode': 'cold' if cold is not None else 'native' if session.get('historyManaged') is True else 'resident'}
        if projected is not None:
            values.update(rows_count=len(projected),
                          **{'rows_' + key: [row.get(key) for row in projected]
                             for key in ('id', 'role', 'text')},
                          rows_actor=[row.get('inputActor') for row in projected],
                          rows_label=[[row.get('agentId'), row.get('agentLabel')] for row in projected])
        if cold is not None:
            values.update(cold_resource=cold.identity, cold_stamp=stamp)
        components.update({key: digest(values.get(key)) for key in SOURCE_COMPONENTS})
    cold, stamp = None, None
    if session.get('historyManaged') is True:
        # Accepted-but-unsubmitted host inputs need not have entered native
        # history yet. Fence that bounded resident suffix before reading the
        # older native public tail; never send these rows to the utility.
        resident = dict.get(session, 'messages', ())
        require_settled_tail(resident[max(0, len(resident) - MAX_ROWS):])
        page = windows.public_native_tail(session)
    else:
        cold = windows._cold_source(session)
        page = None
        if cold is not None:
            page, stamp = windows.public_saved_tail(session, cold)
    if page is not None:
        if page.get('unavailableRanges'):
            raise Unavailable('transcript_unavailable')
        projected = public_rows(display_inputs(service, session, page['items']), bounded=False)
        if config is not None:
            projected = select(projected, config, page['hasEarlier'])
        projected = public_rows(projected)
        observed(projected=projected, cold=cold, stamp=stamp)
        return digest([owner, projected])
    rows = dict.get(session, 'messages', ())
    tail = rows[max(0, len(rows) - MAX_ROWS):]
    projected = public_rows(display_inputs(service, session, tail), bounded=False)
    if config is not None:
        projected = select(projected, config, len(rows) > MAX_ROWS)
    projected = public_rows(projected)
    observed(projected=projected)
    return digest([owner, projected])


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