import asyncio
import json
from pathlib import Path
import sys

import pytest

from amplifier_web.acp_participant import ACPParticipant


def peer(tmp_path, *, resume=True):
    return {'id': 'fixture', 'label': 'External fixture', 'command': sys.executable,
            'args': [str(Path(__file__).parent / 'fixtures/external_acp_agent.py'),
                     str(tmp_path / 'peer'), *([] if resume else ['--no-resume'])]}


def operations(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'peer/operations.jsonl').read_text().splitlines()]


async def deny(_options):
    return {'outcome': {'outcome': 'cancelled'}}


async def test_new_stream_and_same_identity_resume_without_prompt_replay(tmp_path):
    binding, updates = {}, []

    async def save(value):
        binding.update(value)

    async def update(value):
        updates.append(value)

    first = ACPParticipant(peer(tmp_path), str(tmp_path), update=update, permission=deny)
    try:
        await first.connect(binding, save)
        identity = binding['sessionId']
        assert binding['capabilities']['resume'] is True
        assert (await first.prompt('first')).stop_reason == 'end_turn'
        assert ''.join(row['content']['text'] for row in updates) == 'Reply: first'
    finally:
        await first.close()
    second = ACPParticipant(peer(tmp_path), str(tmp_path), update=update, permission=deny)
    try:
        await second.connect(binding, save)
        assert binding['sessionId'] == identity
        assert [row['kind'] for row in operations(tmp_path)] == ['new', 'prompt', 'resume']
        await second.prompt('second')
        assert [row['text'] for row in operations(tmp_path) if row['kind'] == 'prompt'] == ['first', 'second']
    finally:
        await second.close()


async def test_resume_not_advertised_preserves_binding_without_replacement(tmp_path):
    saved = []

    async def save(value):
        saved.append(value)

    async def update(_value):
        pass

    participant = ACPParticipant(peer(tmp_path, resume=False), str(tmp_path), update=update, permission=deny)
    try:
        with pytest.raises(ValueError, match='cannot resume'):
            await participant.connect({'sessionId': 'preserved'}, save)
        assert saved == []
        assert not (tmp_path / 'peer/operations.jsonl').exists()
    finally:
        await participant.close()


async def test_cancel_reaches_exact_session_and_prompt_reports_cancelled(tmp_path):
    binding, progress = {}, asyncio.Event()

    async def save(value):
        binding.update(value)

    async def update(_value):
        progress.set()

    participant = ACPParticipant(peer(tmp_path), str(tmp_path), update=update, permission=deny)
    task = None
    try:
        await participant.connect(binding, save)
        task = asyncio.create_task(participant.prompt('wait-for-stop'))
        await asyncio.wait_for(progress.wait(), 5)
        await participant.cancel()
        assert (await asyncio.wait_for(task, 5)).stop_reason == 'cancelled'
        assert operations(tmp_path)[-1] == {'kind': 'cancel', 'sessionId': binding['sessionId']}
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await participant.close()
