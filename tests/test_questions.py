"""Durable answers cross the same authority boundary in UI, text and voice."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web.service import AppError, AppService


class Runtime:
    def __init__(self):
        self.sent = []
        self.failure = None
        self.gate = None

    async def send(self, session, text, input_id, emit):
        self.sent.append((session['id'], text, input_id))
        if self.gate:
            await self.gate.wait()
        if self.failure:
            raise self.failure

    async def close(self):
        pass


async def create_question(app, sid, **changes):
    args = {'sessionId': sid, 'prompt': 'Which report?', 'required': True,
            'dependency': 'Generate the chosen report', 'options': [
                {'id': 'short', 'label': 'Summary'}, {'id': 'full', 'label': 'Full report'}], **changes}
    return (await app.dispatch('question.create', args))['result']


async def test_pending_survives_restart_and_reads_do_not_start_work(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Waiting task'})
    sid = app._session()['id']
    required = await create_question(app, sid)
    optional = await create_question(app, sid, required=False, dependency='Optional formatting preference')
    await app.dispatch('view.update', {'patch': {'draft': 'Keep typing'}})
    await app.close()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        before = copy.deepcopy(restored.state)
        result = await restored.app_bridge('dispatch', {'action': 'question.list', 'args': {'status': 'pending'}}, sid)
        assert {q['id'] for q in result['result']['items']} == {required['id'], optional['id']}
        assert restored.state == before
        assert restored._session()['status'] == 'idle'
        assert restored._session()['questions'][0]['required']
        assert not runtime.sent
        await restored.dispatch('conversation.send', {'sessionId': sid, 'text': 'Continue an independent read', 'preserveDraft': True})
        assert runtime.sent[0][1] == 'Continue an independent read'
        assert restored.questions.store.get(sid, required['id'])['status'] == 'pending'
        assert restored.state['view']['draft'] == 'Keep typing'
    finally:
        await restored.close()


async def test_answer_routes_once_to_exact_target_preserves_draft_and_selection(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Target'})
        sid = app._session()['id']
        q = await create_question(app, sid)
        with pytest.raises(AppError, match='explicit answer'):
            app.questions.answer_for_dependency(sid, q['id'])
        await app.dispatch('session.create', {'title': 'Selected elsewhere'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'Unsent draft'}})
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': q['revision'], 'optionId': 'full'}
        result = await app.dispatch('question.answer', args, command_id='stable-answer')
        assert result['result']['status'] == 'answered'
        assert result['result']['delivery']['status'] == 'accepted'
        assert app.questions.answer_for_dependency(sid, q['id'])['optionId'] == 'full'
        retry = await app.dispatch('question.answer', args, command_id='stable-answer')
        assert retry['duplicate'] and retry['result']['delivery']['status'] == 'accepted'
        assert len(runtime.sent) == 1
        assert runtime.sent[0][0] == sid
        assert runtime.sent[0][2] == 'question:' + q['id'] + ':answer'
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'Unsent draft'
        assert len([m for m in app._session(sid)['messages'] if m.get('questionId') == q['id']]) == 1
        with pytest.raises(AppError, match='already closed'):
            await app.dispatch('question.answer', args, command_id='different-answer')
        with pytest.raises(AppError, match='different contents'):
            await app.dispatch('question.answer', {**args, 'optionId': 'short'}, command_id='stable-answer')
    finally:
        await app.close()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        retry = await restored.dispatch('question.answer', args, command_id='stable-answer')
        assert retry['duplicate']
        assert retry['result']['delivery']['status'] == 'accepted'
        assert len(runtime.sent) == 1
    finally:
        await restored.close()


async def test_wrong_session_stale_superseded_and_cancelled_answers_are_rejected(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        await app.dispatch('session.create', {})
        other = app._session()['id']
        answer = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'optionId': 'full'}
        with pytest.raises(AppError, match='does not belong'):
            await app.dispatch('question.answer', {**answer, 'sessionId': other})
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'question.answer', 'args': answer}, other)
        with pytest.raises(AppError, match='changed'):
            await app.dispatch('question.answer', {**answer, 'expectedRevision': 2})
        replacement = (await app.dispatch('question.supersede', {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1,
            'prompt': 'Which new report?', 'required': True, 'dependency': 'Updated report', 'allowFreeText': True}))['result']
        assert replacement['supersedes'] == q['id']
        with pytest.raises(AppError, match='closed'):
            await app.dispatch('question.answer', answer)
        assert app.questions.store.get(sid, q['id'])['supersededBy'] == replacement['id']
        with pytest.raises(AppError, match='explicit answer'):
            app.questions.answer_for_dependency(sid, q['id'])
        await app.dispatch('question.cancel', {'sessionId': sid, 'id': replacement['id'], 'expectedRevision': 1})
        with pytest.raises(AppError, match='explicit answer'):
            app.questions.answer_for_dependency(sid, replacement['id'])
        with pytest.raises(AppError, match='closed'):
            await app.dispatch('question.answer', {'sessionId': sid, 'id': replacement['id'], 'expectedRevision': 1, 'text': 'yes'})
        assert not app.runtime.sent
    finally:
        await app.close()


@pytest.mark.parametrize('via', ['chat', 'call'])
async def test_agent_answer_requires_real_user_provenance_and_cannot_mint_approval(tmp_path, via):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        answer = {'id': q['id'], 'expectedRevision': 1, 'optionId': 'short'}
        async def bridge(args):
            return await app.app_bridge('dispatch', {'action': 'question.answer', 'args': args}, sid)
        with pytest.raises(AppError, match='sourceMessageId'):
            await bridge(answer)
        model = app._message(app._session(), 'assistant', 'Summary')
        with pytest.raises(AppError, match='user message'):
            await bridge({**answer, 'sourceMessageId': model['id']})
        if via == 'call':
            await app.record_voice_transcript('user', 'The summary please', voice_id='call-one', item_id='voice-item', session_id=sid)
            source = app._session()['messages'][-1]
        else:
            source = app._message(app._session(), 'user', 'The summary please', inputOrigin='ui')
        app._session()['approvals'] = [{'id': 'permission', 'status': 'pending'}]
        with pytest.raises(AppError, match='Additional properties'):
            await bridge({**answer, 'sourceMessageId': source['id'], 'decision': 'approve'})
        result = await bridge({**answer, 'sourceMessageId': source['id']})
        provenance = result['result']['answer']['provenance']
        assert provenance['messageId'] == source['id'] and provenance['via'] == via
        assert provenance['text'] == 'The summary please'
        if via == 'call':
            assert provenance['voiceId'] == 'call-one'
            assert provenance['voiceItemId'] == 'voice-item'
        assert app._session()['approvals'][0]['status'] == 'pending'
        assert len(app.runtime.sent) == 1
        state = await app.app_bridge('get_state', {}, sid)
        assert state['session']['questions'][0]['status'] == 'answered'
    finally:
        await app.close()


async def test_restart_of_uncertain_delivery_does_not_replay_answer(tmp_path):
    runtime = Runtime()
    runtime.gate = asyncio.Event()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    q = await create_question(app, sid)
    args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'A custom report'}
    task = asyncio.create_task(app.dispatch('question.answer', args, command_id='lost-answer'))
    for _ in range(100):
        if runtime.sent:
            break
        await asyncio.sleep(.001)
    assert runtime.sent
    assert app.questions.store.get(sid, q['id'])['delivery']['status'] == 'sending'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await app.close()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        result = await restored.dispatch('question.answer', args, command_id='lost-answer')
        assert result['duplicate']
        assert result['result']['status'] == 'answered'
        assert result['result']['delivery']['status'] == 'unknown'
        assert len(runtime.sent) == 1
        assert any(i['title'] == 'Answer saved; delivery needs attention' for i in restored.get_state()['attention']['items'])
    finally:
        await restored.close()


async def test_slow_answer_does_not_block_independent_work_or_navigation(tmp_path):
    runtime = Runtime()
    runtime.gate = asyncio.Event()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Custom answer'}
        first = asyncio.create_task(app.dispatch('question.answer', args, command_id='slow'))
        for _ in range(100):
            if runtime.sent:
                break
            await asyncio.sleep(.001)
        await asyncio.wait_for(app.dispatch('view.update', {'patch': {'draft': 'Still typing'}}), 1)
        retry = await app.dispatch('question.answer', args, command_id='slow')
        assert retry['duplicate'] and retry['result']['delivery']['status'] == 'sending'
        runtime.gate.set()
        await first
        assert app.state['view']['draft'] == 'Still typing'
        assert len(runtime.sent) == 1
    finally:
        runtime.gate.set()
        await app.close()


@pytest.mark.parametrize('changes', [
    {'options': [], 'allowFreeText': False},
    {'options': [{'id': 'same', 'label': 'One'}, {'id': 'same', 'label': 'Two'}]},
    {'prompt': '   '}, {'dependency': '   '},
])
async def test_invalid_question_leaves_no_record(tmp_path, changes):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        with pytest.raises(AppError):
            await create_question(app, app._session()['id'], **changes)
        assert app.questions.store.all() == []
    finally:
        await app.close()


async def test_hard_exit_between_saved_answer_and_runtime_ack_recovers_unknown(tmp_path):
    script = '''
import asyncio, json, os, sys
from pathlib import Path
from amplifier_web.service import AppService
class Runtime:
    async def send(self, session, text, input_id, emit):
        os._exit(0)
async def main():
    root = Path(sys.argv[1])
    app = AppService(root, Runtime(), workspace=root)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    q = (await app.dispatch('question.create', {'sessionId':sid, 'prompt':'Which report?', 'required':True, 'dependency':'Choose report'}))['result']
    args = {'sessionId':sid, 'id':q['id'], 'expectedRevision':1, 'text':'Summary'}
    (root / 'retry.json').write_text(json.dumps(args))
    await app.dispatch('question.answer', args, command_id='hard-exit')
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    args = json.loads((tmp_path / 'retry.json').read_text())
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        result = await app.dispatch('question.answer', args, command_id='hard-exit')
        assert result['duplicate'] and result['result']['status'] == 'answered'
        assert result['result']['delivery']['status'] == 'unknown'
        assert 'restarted' in result['result']['delivery']['message']
        assert len([m for m in app._session()['messages'] if m.get('questionId') == args['id']]) == 1
        assert runtime.sent == []
    finally:
        await app.close()


async def test_client_change_keeps_answer_receipt_and_original_provenance(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}
        app.clients.attach('first-browser')
        app.clients.attach('reconnected-browser')
        with app.clients.bind('first-browser'):
            await app.dispatch('question.answer', args, command_id='reconnected-answer')
        with app.clients.bind('reconnected-browser'):
            result = await app.dispatch('question.answer', args, command_id='reconnected-answer')
        assert result['duplicate']
        assert result['result']['answer']['provenance']['clientId'] == 'first-browser'
        assert len(app.runtime.sent) == 1
    finally:
        await app.close()


@pytest.mark.parametrize('answer', [{'optionId': 'missing'}, {'optionId': 'short', 'text': 'Extra'}, {'text': ''}, {'text': 'Unlisted'}])
async def test_invalid_answers_never_close_or_deliver(tmp_path, answer):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid, allowFreeText=False)
        with pytest.raises(AppError):
            await app.dispatch('question.answer', {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, **answer})
        assert app.questions.store.get(sid, q['id'])['status'] == 'pending'
        assert app.runtime.sent == []
    finally:
        await app.close()


async def test_fork_keeps_question_ownership_with_original(tmp_path):
    from amplifier_web.host.storage import SessionStore
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        source = app._session()
        sid = source['id']
        app._message(source, 'user', 'Original work')
        SessionStore.for_app(tmp_path, tmp_path).save(sid, [{'role': 'user', 'content': 'Original work'}], {})
        q = await create_question(app, sid)
        await app.dispatch('session.fork', {'id': sid})
        assert app._session()['id'] != sid
        assert app._session()['questions'] == []
        assert app.questions.store.get(sid, q['id'])['status'] == 'pending'
        assert not app.runtime.sent
    finally:
        await app.close()


async def test_definite_admission_rejection_preserves_answer_without_replay(tmp_path):
    from amplifier_web.runtime import SessionInUseError
    runtime = Runtime()
    runtime.failure = SessionInUseError({'client': 'cli'})
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}
        result = await app.dispatch('question.answer', args, command_id='rejected-delivery')
        assert result['result']['status'] == 'answered'
        assert result['result']['delivery']['status'] == 'rejected'
        assert not app._session()['messages']
        retry = await app.dispatch('question.answer', args, command_id='rejected-delivery')
        assert retry['duplicate'] and retry['result']['delivery']['status'] == 'rejected'
        assert len(runtime.sent) == 1
    finally:
        await app.close()


async def test_agent_cannot_launder_its_own_send_as_a_user_answer(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        await app.app_bridge('dispatch', {'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Summary', 'via': 'call'}}, sid)
        source = app._session()['messages'][-1]
        assert source['role'] == 'user' and source['inputOrigin'] == 'agent'
        with pytest.raises(AppError, match='verified user input'):
            await app.app_bridge('dispatch', {'action': 'question.answer', 'args': {
                'id': q['id'], 'expectedRevision': 1, 'optionId': 'short', 'sourceMessageId': source['id']}}, sid)
        assert app.questions.store.get(sid, q['id'])['status'] == 'pending'
        assert len(app.runtime.sent) == 1
    finally:
        await app.close()
