"""Bounded display-only transcript selection and fresh currentness witnesses."""
import json

from .suggestion_provider import INSTRUCTION, estimate_tokens, format_message, transcript_budget
from .suggestion_settings import digest
from .transcript_window import RAW_LIMIT

UNSETTLED_DELIVERY = {'pending', 'sending', 'failed', 'unknown', 'queued', 'queued_for_update'}
UNSETTLED_INPUT = {'queued', 'working', 'waiting', 'failed', 'unknown'}
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
    if row.get('scheduledRunId') or row.get('approvalId') or row.get('questionId') or row.get('taskInput'):
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
    from .input_status import annotate, identity
    require_settled_tail(rows)
    result = []
    external = bool(session.get('externalAgent'))
    replies, settled_rows = set(), set()
    for row in reversed(rows):
        if row.get('role') == 'assistant' and public_text(row) and text_of(row):
            if exact := identity(row.get('inputId')):
                replies.add(exact)
        elif row.get('role') == 'user' and identity(row.get('inputId')) in replies:
            # Positive evidence requires a complete later reply to this exact
            # input ID. A failed/unknown observed phase is not rewritten.
            settled_rows.add(id(row))
    for row in rows:
        item = row
        if row.get('role') == 'user':
            if external:
                # ACP/shared human inputs do not have native generation events.
                # Their canonical conversation acceptance is the display proof.
                item = {key: value for key, value in row.items() if key != 'inputStatus'}
                if (row.get('delivery') or {}).get('status') != 'accepted':
                    item['_nonpublicInput'] = True
            elif row.get('inputId') or row.get('nativeInputId'):
                item = annotate(session, row)
                # An older native checkpoint can establish public user history
                # without a retained host progress annotation. Explicit phases
                # (including an observed UNKNOWN) still apply.
                if ((row.get('source') == 'native' or id(row) in settled_rows)
                        and (item.get('inputStatus') or {}).get('basis') == 'no_evidence'):
                    item.pop('inputStatus', None)
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
                    if isinstance(receipt, dict) and (
                            str(receipt.get('commandAction', '')).startswith(('coordination.', 'task.', 'assignment.'))
                            or receipt.get('origin') in ('agent', 'system', 'scheduler')):
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
        if not public_text(row):
            continue
        if row.get('role') == 'assistant' and text_of(row):
            return
        if row.get('role') == 'user' and unsettled(row):
            raise Unavailable('input_not_settled')


def unsettled(row):
    return ((row.get('delivery') or {}).get('status') in UNSETTLED_DELIVERY
            or (row.get('inputStatus') or {}).get('phase') in UNSETTLED_INPUT)


def public_text(row):
    metadata = row.get('metadata') or {}
    if not isinstance(metadata, dict):
        return False
    marker = metadata.get('amplifier_input')
    return not (
        (isinstance(marker, dict) and marker.get('kind') == 'service')
        or any(row.get(key) for key in ('partial', 'ephemeral', 'private', 'observation',
                                     'observation_id', '_nonpublicInput', 'draft'))
        or row.get('visibility') in {'internal', 'developer'}
        or row.get('via') in {'observation', 'schedule'}
        or any(metadata.get(key) for key in (
            'ephemeral', 'private', 'live_recovery_job', 'amplifier_visible_reference',
            'amplifier_recovery_reference')))


def text_of(row):
    text = row.get('text')
    return text if isinstance(text, str) and text.strip() else ''


def public_rows(rows):
    """Classify in source order. Exact reply linkage outranks adjacency."""
    result = []
    anchors = {}
    previous_human = False
    for row in rows:
        role = row.get('role')
        if role not in {'user', 'assistant'}:
            continue
        actor = speaker(row)
        visible = public_text(row) and not unsettled(row)
        if role == 'user':
            human = visible and actor['kind'] == 'human'
            if not unsettled(row):
                # An unlinked reply inherits only the adjacent input's
                # provenance, not a human label from farther back in history.
                previous_human = human
        else:
            source = row.get('sourceMessageId')
            exact_input = row.get('inputId')
            if isinstance(source, str) and source:
                human = anchors.get(('message', source), False)
            elif isinstance(exact_input, str) and exact_input:
                human = anchors.get(('input', exact_input), False)
            else:
                human = previous_human
            human = human and visible and actor['kind'] not in {'system', 'unknown'}
        keys = [('message', row.get('id'), role == 'user')]
        if role == 'user':
            keys.extend(('input', identity, True) for identity in
                        (row.get('inputId'), row.get('nativeInputId')) if identity)
        for scope, identity, is_input in set(key for key in keys if isinstance(key[1], str)):
            key = scope, identity
            anchors[key] = key not in anchors and is_input and human
        if not human or not (text := text_of(row)):
            continue
        item = {key: row[key] for key in ('id', 'role', 'agentId', 'agentLabel')
                if isinstance(row.get(key), str)}
        item['text'] = text
        item['inputActor'] = actor
        if role == 'user':
            from .voice_input_projection import public_input
            voice = public_input(text, (row.get('metadata') or {}).get('amplifier_input'))
            if voice:
                item['text'] = voice['text']
        result.append(item)
    return result


def eligible(service, session):
    from .session_navigation import is_top_level
    from .updates import work_paused
    from .update_drain import held
    return (is_top_level(session) and not session.get('scheduledRunId')
            and not work_paused(getattr(service, '_state', {})) and not held(service))


def select(rows, budget):
    from .naming_context import strip_context_payloads
    selected = []
    for row in reversed(rows):
        text = strip_context_payloads(row['text'])
        if not text.strip() or text.strip() == '[omitted payload]':
            if not selected and row['role'] == 'assistant':
                return []
            continue
        # Charge separators and the fixed role label, not internal IDs or labels.
        cost = estimate_tokens(format_message(row['role'], text) + '\n\n', ceiling=budget)
        if cost > budget:
            break
        budget -= cost
        selected.append({**row, 'text': text})
    # Trim leading orphan replies, not the newest whole reply or messages
    # inside the retained suffix. At least one actual exchange is required.
    while selected and selected[-1]['role'] != 'user':
        selected.pop()
    return list(reversed(selected)) if selected and selected[0]['role'] == 'assistant' else []


def witness(service, sid, config=None, *, components=None):
    return _snapshot(service, sid, config, components=components)[2]


def _snapshot(service, sid, config, *, components=None):
    """One authorized bounded suffix, with identical capture/currentness selection."""
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
    cold, stamp, gaps = None, None, []
    if session.get('historyManaged') is True:
        # Accepted-but-unsubmitted host inputs need not have entered native
        # history yet. Fence that bounded resident suffix before reading the
        # older native public tail; never send these rows to the utility.
        resident = dict.get(session, 'messages', ())
        display_inputs(service, session, resident[-RAW_LIMIT:])
        page = windows.public_native_tail(session)
    else:
        cold = windows._cold_source(session)
        page = None
        if cold is not None:
            page, stamp = windows.public_saved_tail(session, cold)
    if page is not None:
        rows, gaps = page['items'], page.get('unavailableRanges') or []
        if gaps and cold is None:
            cutoff = max(gap['end'] for gap in gaps)
            rows = [row for row in rows if row['sourceReference']['byteOffset'] >= cutoff]
    else:
        from .saved_message_window import resident_rows
        rows = dict.get(session, 'messages', ())
        rows, gaps, _ = resident_rows(rows, len(rows), RAW_LIMIT, contiguous=True)
    rows = display_inputs(service, session, rows)
    tail = next((row for row in reversed(rows) if row.get('role') in {'user', 'assistant'}
                 and public_text(row) and not unsettled(row) and text_of(row)), None)
    projected = public_rows(rows)
    # Keep the true selected display tail separate from eligibility and payload.
    # A peer-only/latest ineligible reply cannot be replaced by an older reply.
    matching_tail = (tail and tail['role'] == 'assistant' and projected
                     and projected[-1]['id'] == tail['id'])
    config = config or {}
    budget = config.get('_inputBudget')
    if budget is None:
        budget = transcript_budget(config.get('prompt', INSTRUCTION))
    selected = select(projected, budget) if matching_tail else []
    if gaps and not any(row['role'] == 'user' for row in projected):
        raise Unavailable('transcript_unavailable')
    observed(projected=selected, cold=cold, stamp=stamp)
    return selected, tail and tail['id'], digest([owner, tail and (tail['id'], tail['role']), selected])


async def capture(service, sid, config, expected_last):
    async with service.lock:
        rows, tail, key = _snapshot(service, sid, config)
    if tail is not None and tail != expected_last:
        raise Unavailable('source_changed')
    return rows, key