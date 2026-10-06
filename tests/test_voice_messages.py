"""Bounded host/IPC/presentation checks; native DTU persistence is parent-owned."""
import asyncio
import copy
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.automatic_history import (
    display_identity, display_message, read_transcript, remove_internal_copies,
)
from amplifier_web.conversation_export import messages, snapshot
from amplifier_web.history_query import _rows
from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService
from amplifier_web.shared_state_probe import query
from amplifier_web.voice import VoiceCall, VoiceService
from amplifier_web.voice_messages import (
    is_internal_voice_input, presentation_proof, private_voice_provenance,
    private_voice_snapshot, validated_voice_provenance, voice_fingerprint,
    voice_input_id, voice_provenance,
)
from test_automatic_history import app_factory, files_snapshot, native_session
from test_service import Runtime
from test_voice import Socket

INPUT_ID = 'voice:call_1:delegate-1'
TEXT = ('This is a user message arriving through the voice interface of this same Amplifier conversation. '
        'Host directions\n<voice_reference>\n[{"role":"user","text":"Actual speech"}]'
        '\n</voice_reference>\nCurrent spoken user request:\nTool paraphrase @./not-a-voice-attachment')


def native(text=TEXT, **changes):
    return {'role': 'user', 'content': text,
            'metadata': {'amplifier_input': voice_provenance(INPUT_ID)}, **changes}


@pytest.mark.parametrize('change', [
    {'version': 0}, {'version': 2}, {'version': True}, {'version': 1.0},
    {'version': '1'}, {'version': None}, {'kind': 'service'}, {'source': 'voice'},
    {'id': 'voice:other:delegate-1'}, {'call_id': None},
    {'call_id': 'unified.voice.v2:' + INPUT_ID},
    {'call_id': 'unified.voice.v1:' + INPUT_ID + ':extra'},
    {'call_id': 'unified.voice.v1:voice:call_1:other'},
])
def test_unknown_provenance_is_visible_even_with_exact_wrapper_text(change):
    row = native()
    row['metadata']['amplifier_input'].update(change)
    assert not is_internal_voice_input(row)
    assert display_message(row, 0, {'id': 's'})['text'] == TEXT
    assert validated_voice_provenance(row['metadata']['amplifier_input'], INPUT_ID) is None


@pytest.mark.parametrize('metadata', [None, [], 'marker', {}, {'amplifier_input': []},
                                     {'amplifier_input': {'version': 1, 'kind': 'user',
                                      'source': 'user', 'id': INPUT_ID}}])
def test_unmarked_legacy_and_malformed_native_metadata_stays_visible(metadata):
    row = native(metadata=metadata)
    assert not is_internal_voice_input(row)
    assert display_message(row, 0, {'id': 's'})['text'] == TEXT


def test_only_canonical_user_marker_hides_not_model_or_display_lookalikes():
    row = native()
    original = copy.deepcopy(row)
    assert is_internal_voice_input(row)
    assert display_message(row, 4, {'id': 's'}) is None
    internal = display_message(row, 4, {'id': 's'}, include_internal=True)
    assert internal['text'] == TEXT and internal['id'] == display_identity({'id': 's'}, 4, 'user', TEXT)
    assert row == original
    assert not is_internal_voice_input(native(role='assistant'))
    assert not is_internal_voice_input({'role': 'user', 'text': TEXT, 'voicePresentation': presentation_proof('s', TEXT, INPUT_ID)})
    assert display_message(native(role='assistant'), 4, {'id': 's'})['text'] == TEXT


@pytest.mark.parametrize('call,delegation', [
    ('', 'd'), ('c', ''), ('c:d', 'd'), ('c', 'd:extra'), ('c/d', 'd'),
    ('c', 'd\n'), ('c', 'é'), ('x' * 97, 'd'), ('c', 'x' * 97), (None, 'd'),
])
def test_voice_identity_is_strict_and_bounded(call, delegation):
    with pytest.raises(ValueError):
        voice_input_id(call, delegation)


def test_maximum_identity_and_exact_receipt_bound_private_snapshot():
    assert len(voice_input_id('c' * 96, 'd' * 96)) == 199
    session = {'id': 's', 'messages': []}
    proof = presentation_proof('s', TEXT, INPUT_ID)
    receipt = {'inputId': INPUT_ID, 'sessionId': 's', 'voicePresentation': proof}
    fingerprint = voice_fingerprint('s', TEXT)
    private = private_voice_snapshot(session, TEXT, INPUT_ID, fingerprint, receipt)
    assert private_voice_provenance(private, TEXT, INPUT_ID) == voice_provenance(INPUT_ID)
    assert private_voice_provenance(private, TEXT + 'changed', INPUT_ID) is None
    assert private_voice_provenance({**session, **receipt}, TEXT, INPUT_ID) is None
    assert private_voice_provenance(json.loads(json.dumps(private)), TEXT, INPUT_ID) is None
    for key, value in [('version', 2), ('version', True), ('sessionId', 'other'),
                       ('inputId', 'voice:other:one'), ('fingerprint', 'wrong'), ('call_id', 'wrong')]:
        changed = copy.deepcopy(receipt)
        changed['voicePresentation'][key] = value
        rejected = private_voice_snapshot(session, TEXT, INPUT_ID, fingerprint, changed)
        assert private_voice_provenance(rejected, TEXT, INPUT_ID) is None
    for changed, digest in [({**receipt, 'sessionId': 'other'}, fingerprint),
                            ({**receipt, 'inputId': 'other'}, fingerprint),
                            (receipt, 'wrong'), ({}, fingerprint)]:
        rejected = private_voice_snapshot(session, TEXT, INPUT_ID, digest, changed)
        assert private_voice_provenance(rejected, TEXT, INPUT_ID) is None


@pytest.mark.parametrize('operation', ['send', 'retry'])
async def test_ipc_uses_private_proof_not_client_or_arbitrary_snapshot_fields(operation):
    manager = object.__new__(RuntimeManager)
    manager._start_for_input = AsyncMock()
    manager._request = AsyncMock(return_value={'accepted': True})
    session = {'id': 's', 'messages': [], 'voice_input': voice_provenance(INPUT_ID),
               'voicePresentation': presentation_proof('s', TEXT, INPUT_ID)}
    sender = getattr(manager, operation)
    await sender(session, TEXT, INPUT_ID, AsyncMock())
    assert 'voice_input' not in manager._request.call_args.kwargs
    receipt = {'inputId': INPUT_ID, 'sessionId': 's', 'voicePresentation': session['voicePresentation']}
    private = private_voice_snapshot(session, TEXT, INPUT_ID, voice_fingerprint('s', TEXT), receipt)
    await sender(private, TEXT, INPUT_ID, AsyncMock())
    assert manager._request.call_args.kwargs['voice_input'] == voice_provenance(INPUT_ID)
    assert manager._request.call_args.kwargs['text'] == TEXT


@pytest.mark.parametrize('operation', ['send', 'retry'])
@pytest.mark.parametrize('marker', ['valid', 'missing', 'unknown', 'mismatch', 'malformed'])
async def test_worker_submits_user_input_without_erasing_unknown_envelopes(monkeypatch, operation, marker):
    # A signature-shaped fixture inspects the call site, not DTU persistence.
    def input_value(kind, text, **kwargs):
        kwargs.setdefault('call_id', None)
        return SimpleNamespace(kind=kind, text=text, source='user', **kwargs)
    monkeypatch.setitem(sys.modules, 'amplifier_module_loop_live.runtime', SimpleNamespace(Input=input_value))
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    prepare = AsyncMock(side_effect=lambda coordinator, text, quote, **kwargs: text)
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    context = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda key: context))
    worker.execution = object()
    worker.activation = 'activation'
    worker.runtime = SimpleNamespace(accepted=set(), max_input_chars=100000,
                                     submit=AsyncMock(return_value=INPUT_ID))
    envelope = voice_provenance(INPUT_ID)
    if marker == 'missing':
        envelope = None
    elif marker == 'unknown':
        envelope['version'] = 2
    elif marker == 'mismatch':
        envelope['id'] = 'voice:other:one'
    elif marker == 'malformed':
        envelope = ['not-an-envelope']
    await worker._command_serial({'id': 'ipc', 'op': operation, 'input_id': INPUT_ID,
                                  'text': TEXT, 'voice_input': envelope})
    assert replies == [{'op': 'reply', 'id': 'ipc', 'result': {'accepted': True, 'inputId': INPUT_ID}}]
    submitted = worker.runtime.submit.call_args.args[0]
    assert (submitted.kind, submitted.source, submitted.text, submitted.id) == ('user', 'user', TEXT, INPUT_ID)
    assert submitted.call_id == (voice_provenance(INPUT_ID)['call_id'] if marker == 'valid' else None)
    assert prepare.await_count == (0 if marker == 'valid' else 1)


async def test_private_service_receipt_labels_and_retry_are_bound_not_text_inferred(tmp_path):
    class Capture(Runtime):
        async def send(self, session, text, input_id, emit):
            self.sent.append((private_voice_provenance(session, text, input_id), text, input_id))
        retry = send
    runtime = Capture()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        receipt = await app.voice_delegate(TEXT, INPUT_ID, session['id'], call_id='call_1', delegation_id='delegate-1')
        await asyncio.gather(*list(app.tasks))
        assert receipt['voicePresentation'] == presentation_proof(session['id'], TEXT, INPUT_ID)
        stored = app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (INPUT_ID,)).fetchone()
        assert stored[0] == voice_fingerprint(session['id'], TEXT)
        assert json.loads(stored[1])['voicePresentation'] == receipt['voicePresentation']
        assert session['execution']['turns'][0]['label'] == 'Voice request'
        assert runtime.sent[0] == (voice_provenance(INPUT_ID), TEXT, INPUT_ID)
        await app._send(copy.deepcopy(session), TEXT, INPUT_ID, retry=True)
        assert runtime.sent[-1][0] == voice_provenance(INPUT_ID)
        assert (await app.voice_delegate(TEXT, INPUT_ID, session['id'], call_id='call_1', delegation_id='delegate-1'))['duplicate']
        assert len(runtime.sent) == 2
        typed_id = 'voice:typed:lookalike'
        session['voice_input'] = voice_provenance(typed_id)
        session['voicePresentation'] = presentation_proof(session['id'], TEXT, typed_id)
        await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': TEXT}, command_id=typed_id)
        await asyncio.gather(*list(app.tasks))
        assert runtime.sent[-1] == (None, TEXT, typed_id)
        assert session['execution']['turns'][-1]['label'] == TEXT[:100]
        with pytest.raises(AppError, match='does not match'):
            await app.voice_delegate(TEXT, INPUT_ID, session['id'], call_id='other', delegation_id='one')
    finally:
        await app.close()


async def test_voice_startup_failure_keeps_presentation_proof_for_explicit_retry(tmp_path):
    runtime = SimpleNamespace(send=AsyncMock(side_effect=RuntimeStartupError('fixture failure')), close=AsyncMock())
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        receipt = await app.voice_delegate(TEXT, INPUT_ID, app._session()['id'], call_id='call_1', delegation_id='delegate-1')
        await asyncio.gather(*list(app.tasks))
        saved = json.loads(app.db.execute('SELECT receipt FROM commands WHERE id=?', (INPUT_ID,)).fetchone()[0])
        assert saved['delivery'] == 'failed' and saved['voicePresentation'] == receipt['voicePresentation']
    finally:
        await app.close()


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_actual_voice_path_keeps_public_speech_answer_and_one_delegation(tmp_path, provider):
    class Capture(Runtime):
        async def send(self, session, text, input_id, emit):
            assert private_voice_provenance(session, text, input_id) == voice_provenance(input_id)
            await super().send(session, text, input_id, emit)
    runtime = Capture()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        call = VoiceCall(VoiceService(app), session['id'])
        call.id, call.provider, call.socket = 'call_1', provider, Socket()
        await call.record('user', 'Actual speech', 'spoken-item')
        event = ({'type': 'session.delegation.created', 'event_id': 'e1',
                  'delegation': {'id': 'delegate-1', 'target': 'client'}} if provider == 'live' else
                 {'type': 'response.function_call_arguments.done', 'event_id': 'e1',
                  'name': 'amplifier_delegate', 'call_id': 'delegate-1',
                  'arguments': '{"text":"Tool paraphrase, not a user transcript"}'})
        await call.handle(event)
        await call.handle(event)
        await asyncio.gather(*list(call.tasks))
        assert len(runtime.sent) == 1 and runtime.sent[0][2] == INPUT_ID
        assert len([row for row in session['messages'] if row.get('voiceId') and row['role'] == 'user']) == 1
        assert session['generations'][-1]['text'] == 'Test transport result'
        public, summary = snapshot(tmp_path, session, [
            {'id': 'artifact-one', 'title': 'Retained diagram', 'sessionId': session['id']}])
        assert public.count('\n\nActual speech\n') == 1
        assert 'Tool paraphrase, not a user transcript' not in public
        assert 'Test transport result' in public
        assert 'Retained diagram' in public and summary['artifactCount'] == 1
    finally:
        await app.close()


def test_canonical_paging_export_query_probe_and_exact_cached_hiding_do_not_change_files(tmp_path):
    from amplifier_web.session_files import project_slug
    workspace = tmp_path / 'workspace'
    rows = [native(metadata={}), native(), {'role': 'assistant', 'content': 'Public answer'},
            native(metadata={'amplifier_input': {**voice_provenance(INPUT_ID), 'version': 2}}),
            native(role='assistant'), native(metadata={'amplifier_input': {
                'version': 1, 'kind': 'user', 'source': 'user', 'id': INPUT_ID}})]
    root = native_session(workspace, 'voice-history', rows)
    original = files_snapshot(root)
    digest = hashlib.sha256((root / 'transcript.jsonl').read_bytes()).hexdigest()
    session = {'id': 'public', 'nativeIdentity': 'voice-history', 'nativeProject': project_slug(workspace),
               'workspace': str(workspace), 'messages': []}
    tail = read_transcript(session, limit=2)
    middle = read_transcript(session, before=tail['offset'], limit=2)
    first = read_transcript(session, before=middle['offset'], limit=2)
    assert [row['nativeIndex'] for page in [first, middle, tail] for row in page['messages']] == [0, 2, 3, 4, 5]
    assert (tail['total'], tail['offset'], middle['offset'], first['offset']) == (5, 3, 1, 0)
    assert tail['hiddenMessages'] == {1: display_identity(session, 1, 'user', TEXT)}
    hidden_copy = display_message(rows[1], 1, session, include_internal=True)
    session['messages'] = [hidden_copy, display_message(rows[2], 2, session),
                           {'id': 'voice-public', 'role': 'user', 'text': 'Actual speech',
                            'via': 'call', 'voiceId': 'call_1'}]
    retrieved, _ = _rows(session)
    assert hidden_copy['id'] not in {row['id'] for row in retrieved}
    assert 'voice-public' in {row['id'] for row in retrieved}
    exported = messages(tmp_path, session)
    assert hidden_copy['id'] not in {row['id'] for row in exported}
    assert 'voice-public' in {row['id'] for row in exported}
    assert sum(row['text'] == TEXT for row in exported) == 4
    probed = query({'version': 1, 'op': 'view', 'workspace': str(workspace), 'sessionId': 'voice-history'})
    assert probed['totalMessages'] == 5 and sum(row['text'] == TEXT for row in probed['messages']) == 4
    # Neither a changed cached text nor a changed role is hidden by that index.
    for changes in [{'text': TEXT + ' changed'}, {'role': 'assistant'}]:
        changed = {**hidden_copy, **changes}
        session['messages'] = [changed]
        assert any((row['role'], row['text']) == (changed['role'], changed['text'])
                   for row in _rows(session)[0])
        remove_internal_copies(session, tail['hiddenMessages'])
        assert session['messages'] == [changed]
        with pytest.raises(ValueError, match='rewritten'):
            messages(tmp_path, session)
    session['messages'] = [hidden_copy]
    remove_internal_copies(session, tail['hiddenMessages'])
    assert session['messages'] == []
    assert files_snapshot(root) == original
    assert hashlib.sha256((root / 'transcript.jsonl').read_bytes()).hexdigest() == digest


async def test_paging_removes_only_exact_cached_wrapper_copies(tmp_path, app_factory):
    rows = [{'role': 'user', 'content': 'Earlier public request'}, native(),
            {'role': 'assistant', 'content': 'Public answer'}]
    root = native_session(tmp_path / 'workspace', 'voice-paging', rows)
    original = files_snapshot(root)
    app = app_factory()
    await app.history.refresh()
    session = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'voice-paging')
    tail = read_transcript(session, limit=1)
    session.update(messages=[display_message(rows[1], 1, session, include_internal=True), *tail['messages']],
                   nativeRevision=tail['revision'], sharedHistoryOffset=tail['offset'],
                   sharedHistoryUserTurnOffset=tail['userOffset'], historyLoaded=True)
    await app.history.load(session['id'], before=tail['offset'])
    assert not session.get('historyError')
    assert [row['text'] for row in session['messages']] == ['Earlier public request', 'Public answer']
    assert files_snapshot(root) == original