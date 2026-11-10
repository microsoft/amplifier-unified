"""Small exact-input display receipts in the existing annotation store.

This is not admission, request membership, a model-read receipt or a goal ledger.
Only native root lifecycle and live execution observations can advance it.
"""
import math
import time

PREFIX = 'input-status:'
PHASES = {'queued', 'included', 'working', 'waiting', 'done', 'failed', 'unknown'}
BASES = {'generation_started', 'execution_observed', 'generation_finished',
         'waiting_workers', 'generation_failed', 'generation_detached',
         'steering_queued', 'steering_context', 'steering_failed',
         'steering_unknown', 'human_decision', 'execution_interrupted',
         'no_evidence'}
TERMINAL_RANK = {'generation_finished': 1, 'waiting_workers': 1,
                 'generation_detached': 2, 'generation_failed': 3}


def identity(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def timestamp(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def native_input_id(role, marker):
    if (role == 'user' and isinstance(marker, dict) and type(marker.get('version')) is int
            and marker.get('version') == 1 and marker.get('kind') in ('user', 'steer')):
        return identity(marker.get('id'))
    return None


def input_id(row):
    if row.get('role') != 'user' or row.get('nativeInputAmbiguous'):
        return None
    web, native = identity(row.get('inputId')), identity(row.get('nativeInputId'))
    if web and native and web != native:
        return None
    return web or native


def public(value, expected):
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1
            or value.get('inputId') != expected
            or not isinstance(value.get('phase'), str) or value['phase'] not in PHASES
            or not isinstance(value.get('basis'), str) or value['basis'] not in BASES):
        return None
    result = {key: value[key] for key in ('version', 'inputId', 'phase', 'basis')}
    for key in ('startedAt', 'updatedAt', 'endedAt'):
        if timestamp(value.get(key)) is not None:
            result[key] = value[key]
    if identity(value.get('generationId')):
        result['generationId'] = value['generationId']
    return result


def annotate(session, row):
    """O(1) lookup per requested row; a display ID/text never binds an input."""
    result = {key: value for key, value in row.items() if key != 'inputStatus'}
    exact = input_id(row)
    if exact:
        entry = _entry(session, exact)
        result['inputStatus'] = public(entry.get('inputStatus'), exact) or {
            'version': 1, 'inputId': exact, 'phase': 'unknown', 'basis': 'no_evidence'}
    return result


def _entry(session, exact):
    entry = session.get('messageAnnotations', {}).get(PREFIX + exact, {})
    return entry if isinstance(entry, dict) and public(entry.get('inputStatus'), exact) else {}


def _save(session, exact, status, *, generation_start=None, closed=False):
    # Fixed-size scalar annotation per input; no content, calls, jobs or event tail.
    entry = {'inputStatus': status, '_closed': closed}
    if status.get('basis') in TERMINAL_RANK:
        entry['_terminalRank'] = TERMINAL_RANK[status['basis']]
    if timestamp(generation_start) is not None:
        entry['_generationStartedAt'] = generation_start
    if entry == _entry(session, exact):
        return
    session.setdefault('messageAnnotations', {})[PREFIX + exact] = entry


def _root(session, event):
    return (identity(session.get('id')) is not None and event.get('sessionId') == session.get('id')
            and event.get('rootSessionId') == session.get('id'))


def _current_generation_epoch(session, gen):
    """After Stop, only its existing exact generation/epoch binding can run."""
    revision = session.get('interruptionRevision', 0)
    if revision == 0:
        return True
    active = session.get('collaborationGeneration') or {}
    return (active.get('id') == gen and type(active.get('interruptionRevision')) is int
            and active['interruptionRevision'] == revision)


def generation(session, event):
    """Source observation time orders explicit retries, not late arrival time."""
    if not _root(session, event):
        return
    kind, gen = event.get('event'), identity(event.get('generation_id'))
    at = timestamp(event.get('observedAt'))
    if not gen or at is None:
        return  # Legacy/missing ownership or observation evidence is unknown.
    if kind == 'generation.started':
        revision = event.get('interruptionRevision', 0)
        if type(revision) is not int or revision != session.get('interruptionRevision', 0):
            return  # Source admission epoch, not arrival after Stop.
        exact = identity(event.get('initial_input_id'))
        if not exact:
            return
        entry = _entry(session, exact)
        prior = entry.get('inputStatus', {})
        if prior.get('generationId') == gen:
            return  # Duplicate starts cannot reopen a terminal/lost generation.
        previous_start = entry.get('_generationStartedAt', prior.get('updatedAt'))
        if timestamp(previous_start) is not None and at <= previous_start:
            return
        if entry.get('_closed') and timestamp(prior.get('updatedAt')) is not None and at <= prior['updatedAt']:
            return  # A start observed before Stop/loss cannot restart its timer.
        _save(session, exact, {'version': 1, 'inputId': exact, 'generationId': gen,
            'phase': 'working', 'basis': 'generation_started', 'startedAt': at,
            'updatedAt': at}, generation_start=at)
        return
    phases = {'generation.finished': ('waiting', 'waiting_workers') if event.get('active_job_ids')
              else ('done', 'generation_finished'),
              'generation.failed': ('failed', 'generation_failed'),
              'generation.detached': ('unknown', 'generation_detached')}
    if (kind not in phases or not isinstance(event.get('input_ids'), list)
            or not _current_generation_epoch(session, gen)):
        return
    phase, basis = phases[kind]
    for exact in dict.fromkeys(value for value in event['input_ids'] if identity(value)):
        entry = _entry(session, exact)
        prior = entry.get('inputStatus', {})
        if entry.get('_closed') and prior.get('basis') == 'execution_interrupted':
            continue  # Loss fences all terminals until an explicit newer start.
        if prior.get('generationId') not in (None, gen):
            continue  # An unseen generation terminal cannot order itself after a retry.
        # Failed/detached evidence may correct a success receipt, never vice versa.
        if (entry.get('_terminalRank', TERMINAL_RANK.get(prior.get('basis'), 0)) >= TERMINAL_RANK[basis]
                or timestamp(entry.get('_generationStartedAt')) is not None
                and at < entry['_generationStartedAt']):
            continue
        status = {'version': 1, 'inputId': exact, 'generationId': gen,
                  'phase': phase, 'basis': basis, 'updatedAt': at, 'endedAt': at}
        if timestamp(prior.get('startedAt')) is not None:
            status['startedAt'] = prior['startedAt']
        _save(session, exact, status,
              generation_start=entry.get('_generationStartedAt'), closed=True)


def execution(session, event):
    if (not _root(session, event) or event.get('liveObservation') is not True
            or event.get('canonicalHistory') or event.get('nativeHistory')
            or event.get('lifecycle') in {'background', 'voice'}
            or event.get('kind') not in {'llm', 'tool'}
            or event.get('purpose')
            or event.get('phase') not in {'running', 'working', 'retrying'}):
        return
    exact = identity(event.get('admissionInputId'))
    started = timestamp(event.get('startedAt'))
    if not exact or started is None:
        return
    entry = _entry(session, exact)
    prior = entry.get('inputStatus', {})
    gen = identity(event.get('generationId'))
    if (not _current_generation_epoch(session, gen)
            or entry.get('_closed') or prior.get('generationId') not in (None, gen)
            or timestamp(prior.get('updatedAt')) is not None and started < prior['updatedAt']):
        return
    # The admission cause proves observed execution only. It says nothing about
    # fitted request membership, provider entry or a model reading this input.
    status = {'version': 1, 'inputId': exact, 'phase': 'working',
              'basis': 'execution_observed', 'updatedAt': started,
              'startedAt': prior.get('startedAt', started)}
    if gen:
        status['generationId'] = gen
    _save(session, exact, status, generation_start=entry.get('_generationStartedAt'))


def steering(session, message):
    """Use the existing exact message/target-generation delivery reducer."""
    exact = input_id(message)
    evidence = message.get('steering', {})
    gen = identity(evidence.get('generationId'))
    disposition = evidence.get('disposition')
    mapping = {'queued': ('queued', 'steering_queued'),
               'applied': ('included', 'steering_context'),
               'held': ('failed', 'steering_failed'),
               'unknown': ('unknown', 'steering_unknown')}
    if not exact or not gen or disposition not in mapping or not _current_generation_epoch(session, gen):
        return
    entry = _entry(session, exact)
    prior = entry.get('inputStatus', {})
    corrected_delivery = prior.get('basis') == 'steering_failed' and disposition == 'applied'
    if (entry.get('_closed') and not corrected_delivery or prior.get('generationId') not in (None, gen)
            or prior.get('basis') in {'steering_context', 'execution_observed', 'generation_started'}):
        return
    phase, basis = mapping[disposition]
    at = timestamp(evidence.get('contextAt' if disposition == 'applied' else 'queuedAt'))
    if at is None:
        at = time.time()
    status = {'version': 1, 'inputId': exact, 'generationId': gen,
              'phase': phase, 'basis': basis, 'updatedAt': at}
    _save(session, exact, status, generation_start=entry.get('_generationStartedAt'),
          closed=disposition == 'held')


def human_wait(session, event):
    # Current native approval events usually lack this binding. An ambient
    # pending question or approval is never evidence about the latest message.
    if not _root(session, event) or not identity(event.get('id')):
        return
    exact, gen = identity(event.get('inputId')), identity(event.get('generationId'))
    if not exact or not gen:
        return
    entry = _entry(session, exact)
    prior = entry.get('inputStatus', {})
    if entry.get('_closed') or prior.get('generationId') != gen:
        return
    _save(session, exact, {**prior, 'phase': 'waiting', 'basis': 'human_decision',
                          'updatedAt': time.time()},
          generation_start=entry.get('_generationStartedAt'))


def interrupt(session, *, unbound_only=False):
    """Explicit loss/Stop/restart only; idle and tool failure never imply success."""
    at, changed = time.time(), False
    for key, entry in session.get('messageAnnotations', {}).items():
        if not key.startswith(PREFIX) or not isinstance(entry, dict):
            continue
        status = public(entry.get('inputStatus'), key[len(PREFIX):])
        if not status:
            continue
        if unbound_only and status.get('generationId'):
            continue
        if status.get('phase') not in {'queued', 'included', 'working', 'waiting'}:
            continue
        entry['inputStatus'] = {**status, 'phase': 'unknown',
                               'basis': 'execution_interrupted', 'updatedAt': at,
                               'endedAt': status.get('endedAt', at)}
        entry['_closed'] = True
        changed = True
    return changed