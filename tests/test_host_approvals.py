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
