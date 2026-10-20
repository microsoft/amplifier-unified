"""Canonical worker transport preserves exact labels and modern feedback context."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.host.approvals import Approvals, ApprovalDecision
from amplifier_web.runtime_worker import Worker


@pytest.mark.parametrize('offered', [['Allow', 'Deny'], ['approve', 'reject'], ['deny', 'allow']])
@pytest.mark.parametrize('decision', ['allow', 'deny'])
async def test_real_worker_canonical_decision_returns_exact_offered_label_with_note(monkeypatch, offered, decision):
    from amplifier_web import runtime_worker
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    context = SimpleNamespace(add_message=AsyncMock())
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: context))
    task = asyncio.create_task(Approvals(None, worker.ask).request_approval('Review input', offered))
    try:
        await asyncio.sleep(0)
        requested = next(e for e in events if e.get('type') == 'approval.requested')
        assert set(requested['options']) == {'allow', 'deny'}
        await worker.command({'op': 'approval', 'id': 'human-decision', 'approval_id': requested['id'],
                              'decision': decision, 'comment': 'Use the reviewed copy.'})
        result = await task
        expected = next(label for label in offered if label.casefold() in (
            {'allow', 'approve'} if decision == 'allow' else {'deny', 'reject'}))
        assert result == expected and result.comment == 'Use the reviewed copy.'
        assert not worker.approvals and not worker.approval_contexts
        context.add_message.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('options', [['Allow', 'Deny'], ['approve', 'reject']])
async def test_timeout_returns_offered_denial_even_with_allow_default(monkeypatch, options):
    from amplifier_web import runtime_worker
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    decision = await Approvals(None, worker.ask).request_approval('No answer', options, timeout=.01, default='allow')
    assert decision == options[1] and not worker.approvals
    assert any(e.get('type') == 'approval.resolved' and e.get('decision') == 'expired' for e in events)


@pytest.mark.parametrize('options', [['Proceed', 'Cancel'], ['allow'], ['allow', 'approve', 'deny'], ['allow', 1]])
async def test_unsupported_or_ambiguous_choices_refuse_before_asking(options):
    ask = AsyncMock()
    with pytest.raises(ValueError, match='unambiguous'):
        await Approvals(None, ask).request_approval('Do not dispatch', options)
    ask.assert_not_called()


async def test_context_wrapper_never_invents_connected_transport():
    missing = Approvals(None, None)
    child = missing.for_context(lambda: object())
    assert not missing.interactive and not child.interactive
    with pytest.raises(RuntimeError, match='no user approval surface'):
        await child.request_approval('Disconnected')


async def test_typed_request_keeps_note_and_never_remembers():
    ask = AsyncMock(return_value=ApprovalDecision('deny', 'Use a temporary copy.'))
    request = SimpleNamespace(tool_name='computer', action='type', details={}, timeout=2)
    result = await Approvals(None, ask).request_approval(request)
    assert not result.approved and result.reason == 'Use a temporary copy.' and not result.remember


async def test_typed_allow_and_timeout_never_remember():
    request = SimpleNamespace(tool_name='computer', action='type', details={}, timeout=.01)
    allowed = await Approvals(None, AsyncMock(return_value=ApprovalDecision('allow', 'Reviewed.'))).request_approval(request)
    assert allowed.approved and allowed.reason == 'Reviewed.' and not allowed.remember
    async def unanswered(*args):
        await asyncio.Event().wait()
    denied = await Approvals(None, unanswered).request_approval(request)
    assert not denied.approved and not denied.remember and 'timed out' in denied.reason


async def test_concurrent_alias_note_and_timeout_keep_separate_child_contexts(monkeypatch):
    from amplifier_web import runtime_worker
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    contexts = [SimpleNamespace(add_message=AsyncMock()) for _ in range(2)]
    approvals = Approvals(None, worker.ask)
    first = asyncio.create_task(approvals.for_context(lambda: contexts[0]).request_approval('first', ['Allow', 'Deny']))
    second = asyncio.create_task(approvals.for_context(lambda: contexts[1]).request_approval('second', ['approve', 'reject'], timeout=.01))
    try:
        await asyncio.sleep(0)
        requests = [e for e in events if e.get('type') == 'approval.requested']
        request = next(e for e in requests if e['prompt'] == 'first')
        await worker.command({'op': 'approval', 'id': 'first-reply', 'approval_id': request['id'],
                              'decision': 'allow', 'comment': 'For first child only.'})
        allowed, denied = await asyncio.gather(first, second)
        assert allowed == 'Allow' and allowed.comment == 'For first child only.'
        assert denied == 'reject'
        contexts[0].add_message.assert_awaited_once()
        contexts[1].add_message.assert_not_awaited()
        assert not worker.approvals and not worker.approval_contexts
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize('connected', [False, True])
async def test_common_child_install_advertises_transport_without_permission(monkeypatch, tmp_path, connected):
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host import mentions, prompt_events
    monkeypatch.setattr(mentions, 'install', lambda _: None)
    monkeypatch.setattr(prompt_events, 'install', lambda _: None)
    ask = AsyncMock() if connected else None
    capabilities = {}
    coordinator = SimpleNamespace(register_capability=lambda k, v: capabilities.update({k: v}), get=lambda _: {})
    child = SimpleNamespace(session_id='synthetic-child', coordinator=coordinator)
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), SessionStore(tmp_path / 'children'), Approvals(None, ask))
    await children.install(child, object())
    assert capabilities['approval.interactive'] is connected
    if connected:
        ask.assert_not_called()
