import asyncio
import json
from pathlib import Path
import sys

import pytest

from amplifier_web.acp_participant import ACPParticipant, ATTENTION


def participant(tmp_path, profile='plain'):
    updates, bindings, permissions = [], {}, []

    async def update(value):
        updates.append(value)

    async def permission(options):
        permissions.append(options)
        return {'outcome': {'outcome': 'cancelled'}}

    async def save(value):
        bindings.update(value)

    peer = ACPParticipant({'command': sys.executable, 'args': [str(
        Path(__file__).parent / 'fixtures/external_acp_participation.py'), str(tmp_path / 'peer'), profile]},
        str(tmp_path), update=update, permission=permission)
    return peer, save, bindings, updates, permissions


def operations(tmp_path):
    path = tmp_path / 'peer/operations.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def wait_operation(tmp_path, kind):
    async with asyncio.timeout(5):
        while not any(row['kind'] == kind for row in operations(tmp_path)):
            await asyncio.sleep(.01)


@pytest.mark.parametrize('profile', ['plain', 'disabled', 'wrong-version'])
async def test_unadvertised_attention_never_calls_extension(tmp_path, profile):
    peer, save, _, _, _ = participant(tmp_path, profile)
    try:
        await peer.connect({}, save)
        assert await peer.attention({'trigger_id': 'source'}, 1234) is None
        assert not any(row['kind'] == 'extension' for row in operations(tmp_path))
    finally:
        await peer.close()


async def test_attention_preserves_original_source_and_exact_prompt_ticket(tmp_path):
    peer, save, binding, _, _ = participant(tmp_path, 'attention')
    source = {'conversation_id': 'room', 'thread_id': '', 'trigger_id': 'original',
              'agent': {'id': 'agent', 'is_agent': True},
              'messages': [{'id': 'original', 'sender': 'human', 'text': 'source evidence', 'revision': 'v1'}]}
    try:
        await peer.connect({}, save)
        offer = await peer.attention(source, 1794590000123)
        assert offer['action'] == 'engage'
        ticket = {ATTENTION: {'id': offer['id'], 'ticket': offer['ticket']}}
        await peer.prompt('Execute contribution', context='attributed room', metadata=ticket)
        extension = next(row for row in operations(tmp_path) if row['kind'] == 'extension')
        assert extension['method'] == 'amplifier/attention'
        assert extension['params'] == {'sessionId': binding['sessionId'], 'occurredAt': 1794590000123,
                                       'context': source}
        recorded = next(row for row in operations(tmp_path) if row['kind'] == 'metadata')
        assert recorded['metadata'] == ticket
        assert recorded['sessionId'] == binding['sessionId']
        assert len([row for row in operations(tmp_path) if row['kind'] == 'prompt']) == 1
    finally:
        await peer.close()


async def test_attention_hold_errors_and_malformed_ticket_do_not_fall_back(tmp_path):
    from acp import RequestError
    peer, save, _, _, _ = participant(tmp_path, 'attention')
    try:
        await peer.connect({}, save)
        assert (await peer.attention({'choice': 'hold'}, 1234))['action'] == 'hold'
        with pytest.raises(RequestError):
            await peer.attention({'choice': 'error'}, 1234)
        with pytest.raises(ValueError, match='invalid attention ticket'):
            await peer.attention({'choice': 'malformed'}, 1234)
        with pytest.raises(RequestError):
            await peer.prompt('not accepted', metadata={ATTENTION: {'id': 'offer', 'ticket': 'expired'}})
        assert not any(row['kind'] in ('decision', 'prompt') for row in operations(tmp_path))
        assert len([row for row in operations(tmp_path) if row['kind'] == 'extension']) == 3
    finally:
        await peer.close()


@pytest.mark.parametrize('choice', ['speak', 'pass'])
async def test_quiet_fallback_hides_all_updates_and_permission_then_preserves_session(tmp_path, choice):
    peer, save, binding, updates, permissions = participant(tmp_path)
    try:
        await peer.connect({}, save)
        assert await peer.decide({'choice': choice, 'sourceId': 'original'}) == {
            'decision': choice, 'reason': 'fixture'}
        assert updates == [] and permissions == []
        assert next(row for row in operations(tmp_path) if row['kind'] == 'permission')['outcome'] == {
            'outcome': 'cancelled'}
        if choice == 'speak':
            await peer.prompt('accepted contribution')
            assert ''.join(row['content']['text'] for row in updates) == 'Reply: accepted contribution'
            assert all(row['sessionId'] == binding['sessionId'] for row in operations(tmp_path)
                       if row['kind'] in ('decision', 'prompt'))
        else:
            assert not any(row['kind'] == 'prompt' for row in operations(tmp_path))
    finally:
        await peer.close()


@pytest.mark.parametrize('choice', ['malformed', 'oversized'])
async def test_invalid_decision_is_not_retried_or_executed(tmp_path, choice):
    peer, save, _, updates, _ = participant(tmp_path)
    try:
        await peer.connect({}, save)
        with pytest.raises(ValueError, match='participation decision'):
            await peer.decide({'choice': choice})
        assert updates == []
        assert [row['kind'] for row in operations(tmp_path)] == ['new', 'decision', 'permission']
    finally:
        await peer.close()


async def test_stop_during_quiet_decision_cancels_without_public_output(tmp_path):
    peer, save, binding, updates, _ = participant(tmp_path)
    task = None
    try:
        await peer.connect({}, save)
        task = asyncio.create_task(peer.decide({'choice': 'wait'}))
        await wait_operation(tmp_path, 'permission')
        await peer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert updates == []
        assert operations(tmp_path)[-1] == {'kind': 'cancel', 'sessionId': binding['sessionId']}
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await peer.close()


async def test_awareness_targets_actual_active_prompt_never_starts_idle_work(tmp_path):
    peer, save, _, _, _ = participant(tmp_path, 'attention')
    task = None
    try:
        await peer.connect({}, save)
        assert peer.supports_awareness
        assert await peer.observe('idle peer', input_id='idle') is None
        task = asyncio.create_task(peer.prompt('wait-for-stop'))
        await wait_operation(tmp_path, 'prompt')
        source = {'com.microsoft.amplifier.source-delivery': {
            'version': 1, 'sources': [{'id': 'peer-source', 'revision': 'v1', 'occurredAt': 1234}]}}
        receipt = await peer.observe('Peer evidence, original requester unchanged',
                                     input_id='peer-input', metadata=source)
        assert receipt == {'status': 'injected', '_meta': {'steering': {'inputId': 'peer-input'}}}
        ext = next(row for row in operations(tmp_path) if row['kind'] == 'extension')
        assert ext['params']['_meta']['com.microsoft.amplifier.source-delivery'] == source[
            'com.microsoft.amplifier.source-delivery']
        # Neither a definite idle refusal nor an uncorrelated acknowledgment
        # permits a normal prompt or an automatic resend.
        assert (await peer.observe('idle-refusal', input_id='idle-refused'))['status'] == 'promptRequired'
        with pytest.raises(ValueError, match='awareness input identity'):
            await peer.observe('invalid-receipt', input_id='uncertain')
        await peer.cancel()
        assert (await task).stop_reason == 'cancelled'
        assert await peer.observe('after completion', input_id='late') is None
        assert len([row for row in operations(tmp_path) if row['kind'] == 'prompt']) == 1
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await peer.close()
