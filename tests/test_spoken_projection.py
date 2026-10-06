"""Projection tests only; runtime/browser acceptance is separately retained."""
import copy
import json
import time
import uuid

import pytest

from amplifier_web.automatic_history import display_message
from amplifier_web.browser_detail import page, project, read_text
from amplifier_web.conversation_export import snapshot
from amplifier_web.history_query import _rows
from amplifier_web.service import AppService
from amplifier_web.voice_messages import (
    RESPONSE_METADATA, generation_verdict, project_message, retain_membership,
    retain_receipt,
)
from test_service import Runtime

INPUT = 'voice:original:request'
BINDING = {'commandId': INPUT, 'acceptedInputId': INPUT, 'voiceCallId': 'original'}
BODY = 'Private backend written answer'


def fixture():
    receipt = {'version': 1, 'rootSessionId': 'root', 'generationId': 'g',
        'inputIds': [INPUT], 'appendIds': [str(uuid.uuid4())],
        'bindings': [BINDING], 'ownership': 'exclusive-private-voice'}
    root = {k: receipt[k] for k in ('version', 'rootSessionId', 'generationId', 'inputIds')}
    marker = {**root, 'presentationRole': 'backend-relay', 'bindings': [BINDING],
              'appendId': receipt['appendIds'][0]}
    row = {'id': 'live', 'role': 'assistant', 'text': BODY, 'rootGenerations': [root]}
    session = {'id': 'root', 'messages': [row], 'voiceResponses': {'g': receipt}}
    return session, row, marker, receipt


def test_independent_channels_project_without_join_and_leave_bytes_unchanged():
    session, live, marker, _ = fixture()
    canonical = {'role': 'assistant', 'content': BODY, 'metadata': {RESPONSE_METADATA: marker}}
    before = json.dumps(canonical)
    saved = display_message(canonical, 4, session)
    assert saved['id'] != live['id']
    for row in (live, saved):
        public = project_message(session, row)
        assert public['presentation'] == 'backend-relay'
        assert public['text'] == ''
        assert public['relayTextDetail']['id'] == row['id']
        assert public['writtenFallback']['expanded'] is False
    assert project_message(session, saved)['relayChannel'] == 'saved'
    assert json.dumps(canonical) == before
    assert project_message(session, {'id': 'typed', 'role': 'assistant', 'text': BODY})['text'] == BODY
    assert project_message(session, {**live, 'voiceId': 'actual-spoken'})['text'] == BODY


def test_mixed_after_early_append_is_sticky_against_old_exclusive_receipt():
    session, live, marker, exclusive = fixture()
    saved = display_message({'role': 'assistant', 'content': BODY,
                            'metadata': {RESPONSE_METADATA: marker}}, 0, session)
    retain_membership(session, 'g', [INPUT], [BINDING])
    assert project_message(session, live)['text'] == ''
    retain_membership(session, 'g', [INPUT, 'typed'], [BINDING], terminal=True)
    mixed = {**exclusive, 'inputIds': [INPUT, 'typed'], 'ownership': 'public-mixed'}
    retain_receipt(session, mixed)
    retain_receipt(session, exclusive)
    for row in (live, saved):
        assert project_message(session, row)['text'] == BODY
    assert generation_verdict(session, 'g') == 'public'


def test_final_membership_disagreement_is_public_even_when_all_inputs_look_voice():
    session, live, _, _ = fixture()
    second = 'voice:original:second'
    retain_membership(session, 'g', [INPUT, second],
        [BINDING, {'commandId': second, 'acceptedInputId': second, 'voiceCallId': 'original'}],
        terminal=True)
    assert project_message(session, live)['text'] == BODY


@pytest.mark.parametrize('origin', [None, {}, {'version': 1, 'rootSessionId': 'child',
    'generationId': 'g', 'inputIds': [INPUT]},
    {'version': 1, 'rootSessionId': 'root', 'generationId': 'other', 'inputIds': [INPUT]}])
def test_host_message_spanning_generations_needs_every_origin(origin):
    session, row, _, _ = fixture()
    row['rootGenerations'].append(origin)
    assert project_message(session, row)['text'] == BODY


@pytest.mark.parametrize('change', [
    lambda s, r: s.pop('voiceResponses'),
    lambda s, r: s['voiceResponses']['g'].update(ownership='unconfirmed'),
    lambda s, r: r.update(rootGenerations=[]),
    lambda s, r: r.update(partial=True),
])
def test_crash_missing_receipt_unknown_native_and_interrupted_fail_open(change):
    session, row, _, _ = fixture()
    change(session, row)
    assert project_message(session, row)['text'] == BODY


def test_public_reader_paging_search_export_and_artifact_range_keep_origin(tmp_path):
    session, live, _, _ = fixture()
    spoken = {'id': 'spoken', 'role': 'assistant', 'text': 'Actual spoken words',
              'via': 'call', 'voiceId': 'spoken-item'}
    typed = {'id': 'typed', 'role': 'user', 'text': BODY}
    session['messages'] = [typed, live, spoken]
    before = copy.deepcopy(session)
    assert page(session, 'messages')['items'][1]['text'] == ''
    assert project(session)['messages'][1]['presentation'] == 'backend-relay'
    assert read_text(session, project_message(session, live)['relayTextDetail'])['value'] == BODY
    assert [r['id'] for r in _rows(session)[0]] == ['typed', 'spoken']
    artifact = {'id': 'artifact-original', 'sessionId': 'root', 'messageId': 'live',
                'version': 2, 'title': 'Actual artifact'}
    content, summary = snapshot(tmp_path, session, [artifact],
        {'scope': 'range', 'fromMessageId': 'live', 'throughMessageId': 'spoken'})
    assert BODY not in content
    assert 'Actual spoken words' in content
    assert 'artifact-original' in content and 'live' in content
    assert summary['artifactCount'] == 1 and summary['messageCount'] == 1
    later = {**artifact, 'messageId': 'older', 'publications': [
        {'messageId': 'older', 'version': 1}, {'messageId': 'live', 'version': 2}]}
    content, summary = snapshot(tmp_path, session, [later],
        {'scope': 'range', 'fromMessageId': 'live', 'throughMessageId': 'spoken'})
    assert summary['artifactCount'] == 1 and 'version=2' in content and 'Version `2`' in content
    assert session == before


def test_cached_browser_projection_retains_revision_and_does_not_reintroduce_private_fields():
    from amplifier_web.state_projections import StateProjections
    session, row, _, _ = fixture()
    projected = StateProjections().detail(session)
    assert projected['voicePresentationRevision']
    assert 'voiceResponses' not in projected
    assert projected['messages'][0]['text'] == ''


def test_outcome_is_scoped_to_original_call_session_generation_and_client():
    session, row, _, _ = fixture()
    call = {'clientId': 'browser-a', 'inputIds': [INPUT], 'outcomes': {'g': {
        'sessionId': 'root', 'callId': 'original', 'generationId': 'g',
        'clientId': 'browser-a', 'state': 'relay-failed'}}}
    session['voiceCalls'] = {'original': call}
    assert project_message(session, row)['writtenFallback']['expanded']
    for key, value in [('clientId', 'browser-b'), ('callId', 'new-call'),
                       ('sessionId', 'other'), ('generationId', 'other'), ('state', 'attempted')]:
        changed = copy.deepcopy(session)
        changed['voiceCalls']['original']['outcomes']['g'][key] = value
        assert not project_message(changed, row)['writtenFallback']['expanded']
    session['voiceCalls'] = {'new-call': call}
    assert not project_message(session, row)['writtenFallback']['expanded']


async def test_durable_late_no_attempt_requires_completion_after_original_end(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    sid = str(uuid.uuid4())
    try:
        await app.dispatch('session.create', {'id': sid})
        session = app._session(sid)
        await app.record_voice_relay(sid, 'original', 'client', input_id=INPUT)
        await app.record_voice_relay(sid, 'original', 'client', state='ended')
        session['generations'] = [{'event': 'generation.finished', 'generation_id': 'g',
                                   'input_ids': [INPUT], 'at': time.time() + 1}]
        await app.record_voice_relay(sid, 'original', 'client', generation_id='g', state='late-no-attempt')
        assert session['voiceCalls']['original']['outcomes']['g']['state'] == 'late-no-attempt'
        await app.record_voice_relay(sid, 'original', 'client', generation_id='attempt', state='attempted')
        session['generations'].append({'event': 'generation.finished', 'generation_id': 'attempt',
                                      'input_ids': [INPUT], 'at': time.time() + 1})
        await app.record_voice_relay(sid, 'original', 'client', generation_id='attempt', state='late-no-attempt')
        assert session['voiceCalls']['original']['outcomes']['attempt']['state'] == 'attempted'
        await app.record_voice_relay(sid, 'original', 'client', generation_id='unknown', state='late-no-attempt')
        assert 'unknown' not in session['voiceCalls']['original']['outcomes']
    finally:
        await app.close()
    resumed = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        assert resumed._session(sid)['voiceCalls']['original']['outcomes']['g']['state'] == 'late-no-attempt'
    finally:
        await resumed.close()


def test_retention_overflow_cannot_allow_a_stale_receipt_to_rehide_old_mixed_body():
    session, row, _, exclusive = fixture()
    retain_membership(session, 'g', [INPUT, 'typed'], [BINDING], terminal=True)
    for number in range(201):
        retain_membership(session, f'new-{number}', [INPUT], [BINDING])
    retain_receipt(session, exclusive)
    assert session['voiceMembershipIncomplete']
    assert len(session['voiceMembership']) == 200
    assert project_message(session, row)['text'] == BODY