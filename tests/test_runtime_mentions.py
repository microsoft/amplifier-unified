"""Real Foundation expansion at the host's user-input boundaries, without an LLM."""
from copy import deepcopy
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation.bundle import Bundle
from amplifier_foundation.mentions import BaseMentionResolver
from amplifier_web.host.mentions import expand_input, install


def coordinator(workspace, bundles=None):
    capabilities = {'session.working_dir': str(workspace),
                    'mention_resolver': BaseMentionResolver(base_path=workspace, bundles=bundles or {})}
    value = SimpleNamespace(get_capability=capabilities.get,
                            register_capability=capabilities.__setitem__)
    install(value)
    return value


async def test_workspace_shortcuts_and_composed_namespace_use_shared_expander(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    bundle = tmp_path / 'bundle'
    home = tmp_path / 'amplifier'
    for path in (workspace / '.amplifier', bundle / 'context', home):
        path.mkdir(parents=True)
    (workspace / 'note.md').write_text('WORKSPACE-CONTEXT')
    (workspace / '.amplifier/rules.md').write_text('PROJECT-CONTEXT')
    (home / 'rules.md').write_text('USER-CONTEXT')
    (bundle / 'context/policy.md').write_text('BUNDLE-CONTEXT')
    monkeypatch.setenv('AMPLIFIER_HOME', str(home))
    monkeypatch.chdir(tmp_path)  # The server cwd must not choose the workspace.
    owner = coordinator(workspace, {'foundation': Bundle(name='foundation', base_path=bundle)})
    original = 'Read @note.md @project:rules.md @user:rules.md @foundation:context/policy.md\n'
    expanded = await expand_input(owner, original)
    assert expanded.endswith(original)
    for sentinel in ('WORKSPACE-CONTEXT', 'PROJECT-CONTEXT', 'USER-CONTEXT', 'BUNDLE-CONTEXT'):
        assert expanded.count(sentinel) == 1
    assert '@foundation:context/policy.md' in expanded
    assert owner.get_capability('mention_resolver').resolve('@foundation:context/policy.md') == bundle / 'context/policy.md'


async def test_each_input_sees_current_files_without_accumulating_old_references(tmp_path):
    (tmp_path / 'one.md').write_text('FIRST-VERSION')
    (tmp_path / 'two.md').write_text('SECOND-FILE')
    owner = coordinator(tmp_path)
    assert 'FIRST-VERSION' in await expand_input(owner, '@one.md')
    (tmp_path / 'one.md').write_text('REVISED-VERSION')
    assert 'REVISED-VERSION' in await expand_input(owner, '@one.md')
    expanded = await expand_input(owner, '@two.md @two.md')
    assert expanded.count('SECOND-FILE') == 1
    assert 'VERSION' not in expanded
    assert await expand_input(owner, 'No references') == 'No references'
    assert await expand_input(owner, '@unmounted:policy.md') == '@unmounted:policy.md'
    assert await expand_input(owner, '@missing.md') == '@missing.md'
    assert owner.get_capability('mention_resolver').resolve('@../outside.md') is None
    assert owner.get_capability('mention_resolver').resolve('@one') == tmp_path / 'one.md'
    assert 'REVISED-VERSION' in await expand_input(owner, '@one')


@pytest.mark.parametrize('operation', ['send', 'retry', 'history.edit'])
async def test_worker_expands_inputs_preserving_id_and_attachments(tmp_path, monkeypatch, operation):
    Runtime = pytest.importorskip('amplifier_module_loop_live.runtime').Runtime
    from amplifier_web.runtime_worker import Worker
    (tmp_path / 'note.md').write_text('WORKER-CONTEXT')
    owner = coordinator(tmp_path)
    owner.get = lambda name: SimpleNamespace(get_messages=AsyncMock(return_value=[]))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=owner)
    worker.execution = object()
    worker.runtime = Runtime('session')
    worker.controls = object()
    original = 'Inspect @note.md'
    attachments = [{'path': 'unchanged-image'}]
    outputs = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', outputs.append)
    rewind = AsyncMock(return_value={'accepted': True})
    monkeypatch.setattr('amplifier_web.history_revision.rewind', rewind)
    if operation == 'history.edit':
        args = {'text': original, 'operationId': 'input', 'attachments': attachments}
        data = {'op': 'control', 'operation': operation, 'arguments': args, 'id': 'command'}
    else:
        data = {'op': operation, 'text': original, 'input_id': 'input', 'attachments': attachments, 'id': 'command'}
    before = deepcopy(data)
    await worker._command_serial(data)
    assert 'error' not in outputs[-1], outputs
    item = worker.runtime.inbox.get_nowait()[1]
    assert item.id == 'input' and item.attachments == tuple(attachments)
    assert 'WORKER-CONTEXT' in item.text and item.text.endswith(original)
    assert data == before
    if operation == 'history.edit':
        assert rewind.call_args.args[1]['text'] == original
    else:
        # Delivery confirmation is checked before reading any files again.
        (tmp_path / 'note.md').unlink()
        await worker._command_serial({**data, 'op': 'retry'})
        assert outputs[-1]['result']['duplicate'] is True
        assert worker.runtime.inbox.empty()


@pytest.mark.parametrize('action', ['message', 'steer'])
async def test_worker_messages_use_child_namespace_and_workspace(tmp_path, action):
    Runtime = pytest.importorskip('amplifier_module_loop_live.runtime').Runtime
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore
    parent = tmp_path / 'parent'
    child = tmp_path / 'child'
    parent.mkdir()
    child.mkdir()
    (parent / 'note.md').write_text('PARENT-ONLY')
    (child / 'note.md').write_text('CHILD-CONTEXT')
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), SessionStore(tmp_path / 'sessions'), None)
    runtime = Runtime('child')
    children.rows['child'] = {'sessionId': 'child', 'status': 'idle', 'persistent': True, 'runtime': runtime}
    children.sessions['child'] = SimpleNamespace(coordinator=coordinator(child))
    children._emit = lambda row: None
    result = await children.control('child', action, 'Read @note.md', input_id='receipt')
    command = runtime.inbox.get_nowait()[1]
    assert command.kind == ('user' if action == 'message' else 'steer')
    assert result['inputId'] == command.id == 'receipt'
    assert 'CHILD-CONTEXT' in command.text and 'PARENT-ONLY' not in command.text
    (child / 'note.md').write_text('CHANGED-FILE')
    assert (await children.control('child', action, 'Read @note.md', input_id='receipt'))['inputId'] == 'receipt'
    assert runtime.inbox.empty()
    with pytest.raises(ValueError, match='identity reused'):
        await children.control('child', action, 'Different instruction @note.md', input_id='receipt')
    assert runtime.inbox.empty()


async def test_expanded_mention_keeps_one_original_chat_bubble(tmp_path):
    from amplifier_web.automatic_history import display_message, merge_web_history
    (tmp_path / 'note.md').write_text('ATTACHED-CONTEXT')
    original = 'Read @note.md'
    text = await expand_input(coordinator(tmp_path), original)
    message = {'id': 'original-bubble', 'inputId': 'input', 'role': 'user', 'text': original, 'createdAt': 42}
    session = {'id': 'session', 'messages': [message]}
    native = {'role': 'user', 'content': text,
              'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'input'}}}
    rows = [display_message(native, 0, session)]
    for _ in range(3):
        merge_web_history(session, rows)
        assert len(session['messages']) == 1
        assert all(session['messages'][0][key] == value for key, value in message.items())
    assert native['content'] == text


@pytest.mark.parametrize('fail', [False, True])
async def test_scheduled_input_expands_or_restores_monitor_guard_on_failure(tmp_path, monkeypatch, fail):
    Runtime = pytest.importorskip('amplifier_module_loop_live.runtime').Runtime
    from test_task_continuity import controls
    from amplifier_web.scheduled_input import admit, finish
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    (tmp_path / 'note.md').write_text('SCHEDULE-CONTEXT')
    worker = controls('session')
    worker.coordinator.register_capability('session.working_dir', str(tmp_path))
    install(worker.coordinator)
    await worker.perform('task.create', {'commandId': 'task', 'expectedRevision': 0, 'objective': 'Keep this goal'})
    task = worker.tasks.record()
    goal = deepcopy(worker.coordinator.session_state['goal'])
    runtime = Runtime('session')
    args = {'kind': 'monitor', 'inputId': 'schedule:input', 'text': 'Read @note.md', 'taskId': task['id'], 'taskRevision': task['revision']}
    if fail:
        monkeypatch.setattr('amplifier_web.host.mentions.expand_input', AsyncMock(side_effect=ValueError('fixture failure')))
        with pytest.raises(ValueError, match='fixture failure'):
            await admit(worker, runtime, args)
        assert runtime.inbox.empty()
    else:
        assert (await admit(worker, runtime, args))['accepted']
        command = runtime.inbox.get_nowait()[1]
        assert 'SCHEDULE-CONTEXT' in command.text and command.text.endswith(args['text'])
        assert command.id == args['inputId']
        finish(worker, {'type': 'session.idle'})
    assert worker.coordinator.session_state['goal'] == goal
    assert await worker.coordinator.get_capability('live.continuation_guard')()
    await worker.close()


async def test_oversized_mentioned_file_does_not_rewind_history(tmp_path, monkeypatch):
    Runtime = pytest.importorskip('amplifier_module_loop_live.runtime').Runtime
    from amplifier_web.runtime_worker import Worker
    (tmp_path / 'large.md').write_text('X' * 200001)
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator(tmp_path))
    worker.execution = object()
    worker.runtime = Runtime('session', max_input_chars=200000)
    worker.controls = object()
    outputs = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', outputs.append)
    rewind = AsyncMock(return_value={'accepted': True})
    monkeypatch.setattr('amplifier_web.history_revision.rewind', rewind)
    await worker._command_serial({'op': 'control', 'operation': 'history.edit', 'id': 'edit',
        'arguments': {'text': 'Read @large.md', 'operationId': 'input'}})
    assert 'input limit' in outputs[-1]['error']
    rewind.assert_not_awaited()
    assert len(outputs) == 1 and worker.runtime.inbox.empty()
