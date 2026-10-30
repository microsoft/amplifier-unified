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


@pytest.mark.parametrize('event', ['runtime.delivery', 'runtime.status', 'check'])
async def test_late_question_acceptance_reconciles_without_resend(tmp_path, event):
    runtime = Runtime()
    runtime.failure = TimeoutError('Lost answer acknowledgement')
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        artifact = (await app.dispatch('canvas.show', {
            'kind': 'markdown', 'title': 'Proposal', 'content': '# Version one'}))['result']
        q = await create_question(app, sid, artifacts=[{'id': artifact['id'], 'version': 1}])
        second = await create_question(app, sid, prompt='A separate choice?')
        await app.dispatch('view.update', {'patch': {'draft': 'Independent draft'}})
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': '**Summary**'}
        original = (await app.dispatch('question.answer', args, command_id='lost-ack'))['result']
        assert original['delivery']['status'] == 'unknown'
        input_id = original['delivery']['inputId']
        assert any(i['title'] == 'Answer saved; delivery needs attention' for i in app.get_state()['attention']['items'])
        if event == 'check':
            async def delivery(session, identity):
                assert session['id'] == sid and identity == input_id
                return 'accepted'
            runtime.delivery = delivery
            checked = await app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': input_id})
            assert checked['result']['delivery'] == 'accepted'
        else:
            await app.on_runtime_event(event, {'sessionId': sid, 'inputId': input_id,
                'delivery': 'accepted', 'event': 'input.delivered', 'status': 'working'})
        saved = (await app.dispatch('question.read', {'sessionId': sid, 'id': q['id']}))['result']
        assert saved['delivery']['status'] == 'accepted'
        assert 'message' not in saved['delivery']
        assert {k: v for k, v in saved.items() if k != 'delivery'} == {
            k: v for k, v in original.items() if k != 'delivery'}
        assert next(m for m in app._session()['messages'] if m.get('inputId') == input_id)['delivery']['status'] == 'accepted'
        assert not any(i['title'] == 'Answer saved; delivery needs attention' for i in app.get_state()['attention']['items'])
        listed = (await app.dispatch('question.list', {'sessionId': sid}))['result']['items']
        assert next(r for r in listed if r['id'] == q['id']) == saved
        assert next(r for r in listed if r['id'] == second['id'])['status'] == 'pending'
        duplicate = await app.dispatch('question.answer', args, command_id='lost-ack')
        assert duplicate['duplicate'] and duplicate['result'] == saved
        # Neither an unrelated terminal event nor acceptance claims completion.
        assert app._session()['execution']['turns'][-1]['phase'] != 'completed'
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle', 'event': 'session.idle'})
        assert app.questions.store.get(sid, q['id'])['delivery']['status'] == 'accepted'
        assert len(runtime.sent) == 1 and app.state['view']['draft'] == 'Independent draft'
    finally:
        await app.close()


@pytest.mark.parametrize('finish', ['timeout', 'cancel'])
async def test_question_acceptance_wins_racing_send_finalizer(tmp_path, finish):
    runtime = Runtime()
    runtime.gate = asyncio.Event()
    runtime.failure = TimeoutError('Late timeout')
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    task = None
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}
        task = asyncio.create_task(app.dispatch('question.answer', args, command_id='racing-answer'))
        for _ in range(100):
            if runtime.sent:
                break
            await asyncio.sleep(.001)
        assert len(runtime.sent) == 1
        await app.on_runtime_event('runtime.delivery', {
            'sessionId': sid, 'inputId': runtime.sent[0][2], 'delivery': 'accepted'})
        if finish == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            runtime.gate.set()
            await task
        assert app.questions.store.get(sid, q['id'])['delivery']['status'] == 'accepted'
        assert (await app.dispatch('question.answer', args, command_id='racing-answer'))['result']['delivery']['status'] == 'accepted'
        assert len(runtime.sent) == 1
    finally:
        runtime.gate.set()
        if task and not task.done():
            await task
        await app.close()


@pytest.mark.parametrize('stale_status', ['sending', 'unknown'])
async def test_restart_reconciles_retained_question_acceptance(tmp_path, stale_status):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    q = await create_question(app, sid)
    args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}
    saved = (await app.dispatch('question.answer', args, command_id='before-restart'))['result']
    stale = copy.deepcopy(saved)
    stale['delivery'].update(status=stale_status, message='Old unconfirmed report')
    app.questions.store.put(stale)
    app._publish_changes(sessions={sid})
    await app.close()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        result = (await restored.dispatch('question.read', {'sessionId': sid, 'id': q['id']}))['result']
        assert result['delivery']['status'] == 'accepted'
        assert result['answer'] == saved['answer']
        assert 'message' not in result['delivery']
        assert len(runtime.sent) == 1
    finally:
        await restored.close()


async def test_question_native_evidence_check_is_passive_without_message_projection(tmp_path):
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.session_files import sessions_dir
    runtime = Runtime()
    runtime.failure = TimeoutError('Lost acknowledgement')
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    probe = RuntimeManager()
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        saved = (await app.dispatch('question.answer', {
            'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}))['result']
        input_id = saved['delivery']['inputId']
        app._session()['messages'] = []
        runtime.delivery = probe.delivery
        args = {'sessionId': sid, 'inputId': input_id}
        assert (await app.dispatch('conversation.delivery', args))['result']['delivery'] == 'unknown'
        path = sessions_dir(tmp_path) / sid / 'transcript.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'role': 'user', 'content': 'Synthetic saved answer',
            'metadata': {'amplifier_input': {'id': input_id}}}) + '\n')
        assert (await app.dispatch('conversation.delivery', args))['result']['delivery'] == 'accepted'
        assert app.questions.store.get(sid, q['id'])['delivery']['status'] == 'accepted'
        assert len(runtime.sent) == 1 and not probe.workers
    finally:
        await probe.close()
        await app.close()


async def test_question_unknown_evidence_and_wrong_bindings_do_not_confirm(tmp_path):
    runtime = Runtime()
    runtime.failure = TimeoutError('Unknown outcome')
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        pending = await create_question(app, sid)
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}
        saved = (await app.dispatch('question.answer', args))['result']
        input_id = saved['delivery']['inputId']
        await app.dispatch('session.create', {})
        other = app._session()['id']
        for target, identity in [(other, input_id), (sid, input_id + ':wrong'),
                                 (sid, 'question:' + pending['id'] + ':answer')]:
            await app.on_runtime_event('runtime.delivery', {
                'sessionId': target, 'inputId': identity, 'delivery': 'accepted'})
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle', 'event': 'session.idle'})
        async def failed_probe(session, identity):
            raise TimeoutError('No evidence')
        runtime.delivery = failed_probe
        assert (await app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': input_id}))['result']['delivery'] == 'unknown'
        assert app.questions.store.get(sid, q['id']) == saved
        assert app.questions.store.get(sid, pending['id'])['status'] == 'pending'
        with pytest.raises(AppError, match='changed'):
            await app.dispatch('question.answer', {**args, 'expectedRevision': 2})
        # A stored binding mismatch cannot be repaired by a similar input ID.
        mismatched = copy.deepcopy(saved)
        mismatched['delivery']['inputId'] += ':other'
        app.questions.store.put(mismatched)
        assert not app.questions.accept_delivery(sid, input_id)
        assert app.questions.store.get(sid, q['id']) == mismatched
        assert len(runtime.sent) == 1
    finally:
        await app.close()


@pytest.mark.parametrize('probe_fails', [False, True])
async def test_question_passive_check_uses_acceptance_arriving_during_probe(tmp_path, probe_fails):
    runtime = Runtime()
    runtime.failure = TimeoutError('Lost acknowledgement')
    entered, finish = asyncio.Event(), asyncio.Event()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    task = None
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        saved = (await app.dispatch('question.answer', {
            'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}))['result']
        input_id = saved['delivery']['inputId']
        app._session()['messages'] = []
        async def delivery(session, identity):
            entered.set()
            await finish.wait()
            if probe_fails:
                raise TimeoutError('Probe unavailable')
            return 'unknown'
        runtime.delivery = delivery
        task = asyncio.create_task(app.dispatch('conversation.delivery', {'sessionId': sid, 'inputId': input_id}))
        await asyncio.wait_for(entered.wait(), 2)
        await app.on_runtime_event('runtime.delivery', {
            'sessionId': sid, 'inputId': input_id, 'delivery': 'accepted'})
        finish.set()
        assert (await task)['result']['delivery'] == 'accepted'
        current = app.questions.store.get(sid, q['id'])
        assert current['delivery']['status'] == 'accepted'
        assert current['answer'] == saved['answer'] and len(runtime.sent) == 1
    finally:
        finish.set()
        if task and not task.done():
            await task
        await app.close()


async def test_question_acceptance_lookup_does_not_scan_unrelated_questions(tmp_path, monkeypatch):
    runtime = Runtime()
    runtime.failure = TimeoutError('Lost reply')
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        q = await create_question(app, sid)
        saved = (await app.dispatch('question.answer', {
            'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Summary'}))['result']
        # The changed reducer may project its affected session, never the whole
        # question catalog or unrelated message bodies, even with large history.
        for index in range(200):
            record = {**q, 'id': f'historical-{index}', 'sessionId': 'unrelated',
                      'prompt': 'x' * 8000, 'status': 'cancelled'}
            app.questions.store.put(record)
        reads = []
        original = app.questions.store.all
        def bounded(session_id=None):
            reads.append(session_id)
            assert session_id == sid
            return original(session_id)
        monkeypatch.setattr(app.questions.store, 'all', bounded)
        assert app.questions.accept_delivery(sid, saved['delivery']['inputId'])
        assert reads == [sid]
        assert not app.questions.accept_delivery(sid, saved['delivery']['inputId'])
        assert reads == [sid]
        monkeypatch.setattr(app.questions.store, 'all', original)
    finally:
        await app.close()


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


async def test_question_preserves_markdown_source_and_exact_review_versions(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        saved = (await app.dispatch('canvas.show', {
            'kind': 'html', 'title': 'Review mockup', 'content': '<h1>Original v1</h1>',
        }))['result']
        prompt = '  **Choose** this design?\n\n- First\n- Second\n\n    code\n'
        q = await create_question(app, sid, prompt=prompt, artifacts=[{'id': saved['id'], 'version': 1}])
        assert q['prompt'] == prompt
        reference = q['artifacts'][0]
        assert reference['id'] == saved['id'] and reference['version'] == 1
        assert reference['kind'] == 'html' and reference['title'] == 'Review mockup'
        assert reference['sessionId'] == sid and len(reference['bodyDigest']) == 64
        assert 'content' not in reference and 'body' not in reference
        await app.dispatch('canvas.versions.revise', {
            'id': saved['id'], 'expectedRevision': 1, 'content': '<h1>New v2</h1>', 'title': 'Changed title',
        })
        answer = '  **Yes**.\n\nKeep the `source`.\n'
        result = (await app.dispatch('question.answer', {
            'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': answer,
        }))['result']
        assert result['answer']['text'] == answer
        assert result['answer']['questionRevision'] == 1
        assert result['answer']['artifacts'] == [reference]
        assert result['artifacts'] == [reference]
        assert len(app.runtime.sent) == 1
    finally:
        await app.close()


async def test_question_preview_is_bound_not_a_message_publication(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        saved = (await app.dispatch('canvas.show', {
            'kind': 'markdown', 'title': 'Proposal', 'content': '# Exact first version',
        }))['result']
        q = await create_question(app, sid, artifacts=[{'id': saved['id'], 'version': 1}])
        app.clients.attach('review-browser')
        with app.clients.bind('review-browser'):
            await app.dispatch('session.select', {'id': sid})
            await app.dispatch('canvas.versions.revise', {
                'id': saved['id'], 'expectedRevision': 1, 'content': '# Latest second version',
            })
            args = {'resourceId': saved['id'], 'version': 1, 'questionId': q['id'],
                    'questionContentRevision': 1, 'previewId': 'question-review'}
            preview = (await app.dispatch('canvas.views.inline', args))['result']
            assert app.canvas_views.canvas(preview['viewId'])['content'] == '# Exact first version'
            assert not app.runtime.sent
            assert app.questions.store.get(sid, q['id'])['status'] == 'pending'
            with pytest.raises(AppError, match='question'):
                await app.dispatch('canvas.views.inline', {**args, 'version': 2})
            with pytest.raises(AppError, match='question'):
                await app.dispatch('canvas.views.inline', {**args, 'questionContentRevision': 2})
            with pytest.raises(AppError):
                await app.dispatch('canvas.views.inline', {**args, 'messageId': 'not-a-publication'})
            await app.dispatch('question.answer', {
                'sessionId': sid, 'id': q['id'], 'expectedRevision': 1, 'text': 'Proceed',
            })
            with pytest.raises(AppError, match='existing preview'):
                await app.dispatch('canvas.views.inline', {**args, 'previewId': 'second-location'})
            await app.dispatch('canvas.views.release', {key: preview[key] for key in (
                'viewId', 'resourceId', 'resourceRevision', 'generation')})
            again = (await app.dispatch('canvas.views.inline', {**args, 'previewId': 'history-review'}))['result']
            assert app.canvas_views.canvas(again['viewId'])['content'] == '# Exact first version'
    finally:
        await app.close()


@pytest.mark.parametrize('invalid', [
    [{'id': 'absent', 'version': 1}],
    [{'id': 'https://example.com/', 'version': 1}],
    [{'id': 'absent', 'version': 0}],
])
async def test_invalid_question_review_reference_never_creates_question(tmp_path, invalid):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        with pytest.raises(AppError):
            await create_question(app, app._session()['id'], artifacts=invalid)
        assert app.questions.store.all() == []
        assert app.runtime.sent == []
    finally:
        await app.close()


async def test_question_review_foreign_owner_supersede_and_dirty_guard(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        saved = (await app.dispatch('canvas.show', {
            'kind': 'html', 'title': 'Review', 'content': '<p>Exact v1</p>',
        }))['result']
        refs = [{'id': saved['id'], 'version': 1}]
        q = await create_question(app, sid, artifacts=refs)
        await app.dispatch('session.create', {})
        other = app._session()['id']
        with pytest.raises(AppError, match='calling conversation'):
            await create_question(app, other, artifacts=refs)
        replacement = (await app.dispatch('question.supersede', {
            'sessionId': sid, 'id': q['id'], 'expectedRevision': 1,
            'prompt': '**Revised** question', 'required': True, 'dependency': 'Choose',
            'artifacts': refs,
        }))['result']
        assert replacement['id'] != q['id']
        assert replacement['artifacts'] == q['artifacts']
        assert app.questions.store.get(sid, q['id'])['status'] == 'superseded'
        app.clients.attach('review-browser')
        with app.clients.bind('review-browser'):
            await app.dispatch('session.select', {'id': sid})
            preview = (await app.dispatch('canvas.views.inline', {
                'resourceId': saved['id'], 'version': 1, 'questionId': replacement['id'],
                'questionContentRevision': 1, 'previewId': 'dirty-review',
            }))['result']
            target = {key: preview[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
            await app.dispatch('canvas.views.dirty', {**target, 'dirty': True})
            with pytest.raises(AppError) as refused:
                await app.dispatch('canvas.select', {'id': saved['id'], 'version': 1})
            assert refused.value.code == 'canvas_view_dirty'
            assert app.canvas_views.canvas(preview['viewId'])['content'] == '<p>Exact v1</p>'
            assert app.runtime.sent == []
    finally:
        await app.close()


async def test_question_preview_revalidates_saved_binding_on_resource_read(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        saved = (await app.dispatch('canvas.show', {
            'kind': 'markdown', 'title': 'Review', 'content': '# Pinned',
        }))['result']
        q = await create_question(app, sid, artifacts=[{'id': saved['id'], 'version': 1}])
        app.clients.attach('reader')
        with app.clients.bind('reader'):
            await app.dispatch('session.select', {'id': sid})
            preview = (await app.dispatch('canvas.views.inline', {
                'resourceId': saved['id'], 'version': 1, 'questionId': q['id'],
                'questionContentRevision': q['contentRevision'], 'previewId': 'read-recheck',
            }))['result']
            assert app.canvas_views.canvas(preview['viewId'])['content'] == '# Pinned'
            # Synthetic corruption: a changed reference must never become a new
            # body on an existing view. Production questions are immutable.
            changed = app.questions.store.get(sid, q['id'])
            changed['artifacts'][0]['bodyDigest'] = '0' * 64
            app.questions.store.put(changed)
            with pytest.raises(AppError, match='saved artifact changed'):
                app.canvas_views.canvas(preview['viewId'])
            assert preview['viewId'] not in {v['viewId'] for v in app.canvas_views.project()['views']}
            assert app.runtime.sent == []
    finally:
        await app.close()


@pytest.mark.parametrize('question_first', [True, False])
async def test_question_and_message_preview_do_not_duplicate_body(tmp_path, question_first):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        message = app._message(app._session(), 'user', 'Review this document')
        saved = (await app.dispatch('canvas.show', {
            'kind': 'text', 'title': 'Review', 'content': 'One body',
        }))['result']
        q = await create_question(app, sid, artifacts=[{'id': saved['id'], 'version': 1}])
        question = {'questionId': q['id'], 'questionContentRevision': q['contentRevision']}
        publication = {'messageId': message['id']}
        first, second = (question, publication) if question_first else (publication, question)
        app.clients.attach('reader')
        with app.clients.bind('reader'):
            await app.dispatch('session.select', {'id': sid})
            args = {'resourceId': saved['id'], 'version': 1, 'presentationRequest': True}
            await app.dispatch('canvas.select', {'id': saved['id'], 'version': 1, **first, 'presentation': 'inline'})
            opened = (await app.dispatch('canvas.views.inline', {**args, **first, 'previewId': 'first'}))['result']
            await app.dispatch('canvas.select', {'id': saved['id'], 'version': 1, **second, 'presentation': 'inline'})
            with pytest.raises(AppError, match='Unknown'):
                app.canvas_views.canvas(opened['viewId'])
            with pytest.raises(AppError, match='presentation changed'):
                await app.dispatch('canvas.views.inline', {**args, **first, 'previewId': 'late-first'})
            replacement = (await app.dispatch('canvas.views.inline', {**args, **second, 'previewId': 'second'}))['result']
            assert app.canvas_views.canvas(replacement['viewId'])['content'] == 'One body'
    finally:
        await app.close()


@pytest.mark.parametrize('action', ['question.answer', 'question.cancel', 'question.supersede'])
@pytest.mark.parametrize('other_client', [False, True])
async def test_question_closure_keeps_dirty_preview_and_has_no_answer_effect(tmp_path, action, other_client):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        saved = (await app.dispatch('canvas.show', {
            'kind': 'html', 'title': 'Question review', 'content': '<p>Exact review</p>',
        }))['result']
        q = await create_question(app, sid, artifacts=[{'id': saved['id'], 'version': 1}])
        unrelated = await create_question(app, sid, prompt='Unrelated question')
        for identity in ('preview-client', 'answer-client'):
            app.clients.attach(identity)
            with app.clients.bind(identity):
                await app.dispatch('session.select', {'id': sid})
        with app.clients.bind('preview-client'):
            view = (await app.dispatch('canvas.views.inline', {
                'resourceId': saved['id'], 'version': 1, 'questionId': q['id'],
                'questionContentRevision': q['contentRevision'], 'previewId': 'owned-review',
            }))['result']
            target = {key: view[key] for key in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
            await app.dispatch('canvas.views.dirty', {**target, 'dirty': True})
        args = {'sessionId': sid, 'id': q['id'], 'expectedRevision': q['revision']}
        args.update({'text': 'Proceed'} if action == 'question.answer' else
                    {'prompt': 'Replacement', 'required': True, 'dependency': 'Choose'} if action == 'question.supersede' else {})
        with app.clients.bind('answer-client' if other_client else 'preview-client'):
            before = app.questions.store.all(sid)
            with pytest.raises(AppError) as caught:
                await app.dispatch(action, args)
            assert caught.value.code == 'canvas_view_dirty'
            assert app.questions.store.all(sid) == before
            assert not app.runtime.sent
            assert not any(m.get('questionId') == q['id'] for m in app._session(sid)['messages'])
            # The guard is question-scoped, not a blanket hold on unrelated decisions.
            await app.dispatch('question.cancel', {
                'sessionId': sid, 'id': unrelated['id'], 'expectedRevision': unrelated['revision'],
            })
        with app.clients.bind('preview-client'):
            assert app.canvas_views.summary(view['viewId'])['dirty'] is True
            assert app.canvas_views.canvas(view['viewId'])['content'] == '<p>Exact review</p>'
            with pytest.raises(AppError) as caught:
                await app.dispatch('session.draft', {})
            assert caught.value.code == 'canvas_view_dirty'
            # Only an explicit renderer clear permits closing the question.
            await app.dispatch('canvas.views.dirty', {**target, 'dirty': False})
        with app.clients.bind('answer-client' if other_client else 'preview-client'):
            await app.dispatch(action, args)
        closed = app.questions.store.get(sid, q['id'])
        assert closed['revision'] == q['revision'] + 1
        assert closed['contentRevision'] == q['contentRevision']
        assert closed['status'] == {'question.answer': 'answered', 'question.cancel': 'cancelled',
                                    'question.supersede': 'superseded'}[action]
        assert len(app.runtime.sent) == (1 if action == 'question.answer' else 0)
        with app.clients.bind('preview-client'):
            assert app.canvas_views.canvas(view['viewId'])['content'] == '<p>Exact review</p>'
            for index in range(11):
                await create_question(app, sid, prompt=f'Later question {index}')
            assert q['id'] in {row['id'] for row in app._session(sid)['questions']}
            await app.dispatch('canvas.views.release', target)
            await app.dispatch('session.draft', {})
    finally:
        await app.close()
