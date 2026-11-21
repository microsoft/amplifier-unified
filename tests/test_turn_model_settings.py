from copy import deepcopy
from types import SimpleNamespace

import pytest

from amplifier_web.errors import AppError
from amplifier_web.next_message_configuration import save
from amplifier_web.runtime import normalize_event
from amplifier_web.turn_model_settings import binding_proof, capture, public_receipt, record
from test_next_message_configuration import app

A = {'instance': 'work', 'model': 'one', 'effort': 'high'}
B = {'instance': 'work', 'model': 'two'}
KEY = 'actual mounted connection identity'


def test_capture_matches_effective_request_and_omits_private_configuration():
    state = {'selection': A, 'connection': KEY, 'credentials': 'secret'}
    capabilities = {'web.context_transition.selection': lambda: state,
                    'web.provider_catalog': {'connectionKeys': {'work': KEY}}}
    coordinator = SimpleNamespace(get_capability=capabilities.get)
    value = capture(coordinator, 'work', 'one', 'input')
    assert value == {'selection': A, 'pinned': True, 'bindingProof': binding_proof('input', KEY)}
    assert capture(coordinator, 'wrong-account', 'one', 'input') is None
    assert capture(coordinator, 'work', 'other', 'input') is None
    state['connection'] = 'different'
    assert capture(coordinator, 'work', 'one', 'input') is None
    state['selection'] = None
    assert capture(coordinator, 'work', 'two', 'input')['selection'] == B
    assert not capture(coordinator, 'work', 'two', 'input')['pinned']
    assert public_receipt({**value, 'credentials': 'secret', 'bindingProof': 'arbitrary text',
                           'selection': {**A, 'endpoint': 'url'}}, 'one') == {'selection': A, 'pinned': True}


def setup_turn(session, input_id, settings, *, pinned=True):
    generation = input_id + '-generation'
    session['collaborationGeneration'] = {'id': generation, 'interruptionRevision': session.get('interruptionRevision', 0)}
    turn = {'id': input_id, 'inputId': input_id, 'runtimeOwnerId': 'owner',
            'phase': 'completed', 'generationId': generation}
    session.setdefault('execution', {}).setdefault('turns', []).append(turn)
    event = {'kind': 'llm', 'phase': 'running', 'admissionInputId': input_id, 'generationId': generation,
             'sessionId': session['id'], 'rootSessionId': session['id'], 'runtimeOwnerId': 'owner',
             'model': settings['model'], 'turnModelSettings': {'selection': settings, 'pinned': pinned,
                                                              'bindingProof': binding_proof(input_id, KEY)}}
    return turn, event


def test_only_first_owned_root_foreground_dispatch_per_generation_anchors_change():
    session = {'id': 's', 'bundle': 'base'}
    turn, event = setup_turn(session, 'a', A)
    record(session, event)
    assert 'modelChange' not in turn
    turn, event = setup_turn(session, 'b', B)
    for invalid in ({'sessionId': 'child'}, {'purpose': 'compaction'}, {'runtimeOwnerId': 'stale'},
                    {'generationId': 'stale'}, {'phase': 'error'}, {'lifecycle': 'background'}):
        record(session, {**event, **invalid})
        assert session['previousTurnSettings']['selection'] == A
        assert 'modelChange' not in turn
    session['nextMessageImpact'] = {'source': {'instance': 'work', 'model': 'one'}, 'coldModels': True}
    record(session, event)
    assert turn['modelChange'] == {'previous': A, 'current': B, 'impact': 'cold-request'}
    record(session, {**event, 'turnModelSettings': {'selection': A, 'pinned': True}})
    assert session['previousTurnSettings']['selection'] == B
    turn, event = setup_turn(session, 'c', B)
    record(session, event)
    assert 'modelChange' not in turn


def test_unknown_bindings_historical_usage_and_unrelated_worker_receipts():
    session = {'id': 's', 'bundle': 'base', '_lastPromptUsage': {'instance': 'work', 'model': 'one'}}
    turn, event = setup_turn(session, 'a', B)
    wire_kind = {'type': 'execution.event', 'event': event}
    _, public = normalize_event(wire_kind, 's')
    assert public['turnModelSettings'] == event['turnModelSettings']
    assert 'turnModelSettings' not in normalize_event(wire_kind, 'other')[1]
    record(session, {**event, 'turnModelSettings': {'selection': B, 'pinned': False}})
    # The legacy usage may predate an intervening failed call on another model.
    assert 'modelChange' not in turn
    assert session['previousTurnSettings'] == {'selection': B}
    turn, event = setup_turn(session, 'b', A)
    record(session, event)
    assert turn['modelChange'] == {'previous': B, 'current': A}
    assert session['previousTurnSettings']['inputId'] == 'b'


async def test_public_restore_revisions_bindings_and_scope(app, monkeypatch):
    session = app._session()
    monkeypatch.setattr('amplifier_web.provider_catalog.configured_binding', lambda *a, **kw: KEY)
    _, event = setup_turn(session, 'a', A)
    record(session, event)
    session['collaborationGeneration']['terminal'] = True
    args = {'sessionId': session['id'], 'expectedRevision': 0}
    session['draft'] = {'text': 'keep my words'}
    save(app, {**args, 'selection': B, 'bundle': 'future'})
    update = {**args, 'expectedRevision': 1, 'restorePreviousInputId': 'a', 'restorePreviousGenerationId': 'a-generation'}
    for patch in ({'expectedRevision': 0}, {'restorePreviousGenerationId': 'reused-input'}, {'restorePreviousInputId': 'old'}):
        with pytest.raises(AppError):
            save(app, {**update, **patch})
    original_target = deepcopy(session['_previousTurnBinding']['target'])
    session['_previousTurnBinding']['target']['executionRevision'] += 1
    with pytest.raises(AppError):
        save(app, update)
    session['_previousTurnBinding']['target'] = original_target
    monkeypatch.setattr('amplifier_web.provider_catalog.configured_binding', lambda *a, **kw: 'different key')
    with pytest.raises(AppError, match='connection'):
        save(app, update)
    assert session['nextMessageRevision'] == 1 and session['pendingModelSelection'] == B
    monkeypatch.setattr('amplifier_web.provider_catalog.configured_binding', lambda *a, **kw: KEY)
    await app.dispatch('conversation.configure', update)
    assert session['selection'] == A and session['bundle'] == 'anchors'
    assert session['modelConnectionKey'] == KEY and session['nextMessageRevision'] == 2
    assert 'pendingModelSelection' not in session and 'pendingBundleSelection' not in session
    assert session['draft'] == {'text': 'keep my words'}


@pytest.mark.parametrize('pinned', [True, False])
async def test_restore_in_flight_preserves_frozen_inputs_and_previous_default_or_explicit_effort(app, monkeypatch, pinned):
    session = app._session()
    monkeypatch.setattr('amplifier_web.provider_catalog.configured_binding', lambda *a, **kw: KEY)
    _, event = setup_turn(session, 'a', B, pinned=pinned)
    record(session, event)
    args = {'sessionId': session['id'], 'expectedRevision': 0}
    save(app, {**args, 'selection': A, 'bundle': 'new'})
    frozen = deepcopy(session['pendingBundleSelection'])
    save(app, {**args, 'expectedRevision': 1, 'restorePreviousInputId': 'a', 'restorePreviousGenerationId': 'a-generation'})
    assert frozen['bundle'] == 'new'
    assert session['nextMessageStatus'] == 'saved'
    assert session['pendingBundleSelection']['bundle'] == 'anchors'
    assert session['pendingBundleSelection']['resetModel'] is (not pinned)
    assert session['previousTurnSettings']['restorePending'] is True
    duplicate = save(app, {**args, 'expectedRevision': 2, 'restorePreviousInputId': 'a',
                           'restorePreviousGenerationId': 'a-generation'})
    assert duplicate['revision'] == 2
    if pinned:
        assert session['pendingModelSelection'] == B
    else:
        assert 'pendingModelSelection' not in session and session['selection'] is None
    save(app, {**args, 'expectedRevision': 2, 'selection': A})
    assert 'restorePending' not in session['previousTurnSettings']
