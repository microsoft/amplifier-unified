"""Optional public-agent profile over the real SDK and service owners, offline."""
import asyncio
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from amplifier_web.agent_instance import NAMESPACE, room_source, session_metadata, prompt_metadata
from test_acp_server import Peer, host, finish
from test_coordination_generation import source_plan, until


def configure(host):
    app, _, _, path = host
    app['service'].server_config['agentInstance'] = {
        'id': 'urn:example:agent:U', 'label': 'Unified agent',
        'workspace': str(path), 'bundle': 'foundation',
        'sources': [room_source('R'), room_source('S')],
    }


async def connect_profile(host, *, negotiate=True):
    app, client, _, path = host
    socket = await client.ws_connect('/api/acp', params={'workspace': str(path), 'bundle': 'foundation'},
        headers={'Authorization': 'Bearer ' + app['control_token']})
    peer = Peer(socket)
    response = await peer.call('initialize', protocolVersion=1,
                              **({'_meta': {NAMESPACE: {'version': 1}}} if negotiate else {}))
    return peer, response


async def new(peer, path, room):
    result = await peer.call('session/new', cwd=str(path), mcpServers=[],
        _meta=session_metadata('urn:example:agent:U', room_source(room)))
    return result


def metadata(room='R', identity='original', revision=0, epoch=0):
    return prompt_metadata('urn:example:agent:U', room_source(room), identity,
        revision=revision, actor='person:fixture', audience=['conversation:' + room],
        reply_to=room, interruption_revision=epoch)


async def prompt(peer, sid, text='Explicit source input', **options):
    return await peer.call('session/prompt', sessionId=sid,
        prompt=[{'type': 'text', 'text': text}], _meta=metadata(**options))


async def inspect_input(peer, sid, **options):
    return await peer.call('_amplifier/instance', sessionId=sid,
        binding=metadata(**options)[NAMESPACE], operation='inspectInput')


async def test_optional_profile_plain_acp_and_negotiation(host):
    configure(host)
    app, _, runtime, path = host
    plain, result = await connect_profile(host, negotiate=False)
    assert NAMESPACE not in result['result'].get('_meta', {})
    assert 'error' in await new(plain, path, 'R')  # No unsolicited extension.
    sid = await plain.new(path)
    assert sid and len(app['service'].state['sessions']) == 1
    await plain.socket.close()
    peer, result = await connect_profile(host)
    profile = result['result']['_meta'][NAMESPACE]
    assert profile['agentId'] == 'urn:example:agent:U'
    assert profile['inputInspection'] is True
    assert 'error' in await peer.call('session/new', cwd=str(path), mcpServers=[])
    assert not runtime.sent
    await peer.socket.close()


async def test_same_public_agent_two_sources_independent_concurrent_contexts(host):
    configure(host)
    app, _, runtime, path = host
    a, init_a = await connect_profile(host)
    b, init_b = await connect_profile(host)
    first, second = await new(a, path, 'R'), await new(b, path, 'S')
    ra, sb = first['result']['sessionId'], second['result']['sessionId']
    assert ra != sb
    assert init_a['result']['_meta'][NAMESPACE]['agentId'] == init_b['result']['_meta'][NAMESPACE]['agentId']
    service = app['service']
    for sid, model in ((ra, 'model-A'), (sb, 'model-B')):
        session = service._session(sid)
        source_plan(service, session)
        session['selection']['model'] = model
        (service.data_dir / 'sessions' / sid / 'control-state.json').write_text(
            json.dumps({'selection': session['selection']}))
    one = asyncio.create_task(prompt(a, ra))
    two = asyncio.create_task(prompt(b, sb, room='S'))
    await until(lambda: len(runtime.sent) == 2 or one.done() or two.done())
    assert len(runtime.sent) == 2, [task.result() if task.done() else 'pending' for task in (one, two)]
    assert len({row[2] for row in runtime.sent}) == 2
    assert service._session(ra)['selection']['model'] == 'model-A'
    assert service._session(sb)['selection']['model'] == 'model-B'
    for sid, _, input_id in runtime.sent:
        await finish(service, sid, input_id)
    assert (await one)['result']['_meta'][NAMESPACE]['delivery'] == 'accepted'
    assert (await two)['result']['_meta'][NAMESPACE]['delivery'] == 'accepted'
    assert not service.access_mode.full(service._session(ra))
    await a.socket.close()
    await b.socket.close()


async def test_lost_ack_inspect_exact_revision_conflict_and_source_fence(host):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    created = await new(peer, path, 'R')
    sid = created['result']['sessionId']
    source_plan(service, service._session(sid))
    runtime.send_ack.clear()
    pending = asyncio.create_task(prompt(peer, sid))
    await until(lambda: len(runtime.sent) == 1)
    input_id = runtime.sent[0][2]
    await peer.socket.close()
    await asyncio.gather(pending, return_exceptions=True)
    await until(lambda: not app['acp_connections'])
    runtime.send_ack.set()
    await finish(service, sid, input_id)
    peer, _ = await connect_profile(host)
    resumed = await peer.call('session/resume', sessionId=sid, cwd=str(path), mcpServers=[],
        _meta=session_metadata('urn:example:agent:U', room_source('R')))
    assert resumed['result']['_meta'][NAMESPACE]['source'] == room_source('R')
    recovered = (await inspect_input(peer, sid))['result']
    assert recovered['delivery'] == 'accepted' and recovered['replayed'] is False
    assert recovered['commandId'] == input_id
    duplicate = await prompt(peer, sid)
    assert duplicate['error']['data']['inputReceipt']['commandId'] == input_id
    conflict = await prompt(peer, sid, text='Changed but claims original revision')
    assert 'different contents' in conflict['error']['message']
    assert len(runtime.sent) == 1
    assert 'error' in await inspect_input(peer, sid, room='S')
    assert 'error' in await new(peer, path, 'unauthorized')
    absent = (await inspect_input(peer, sid, identity='never-delivered'))['result']
    assert absent['delivery'] == 'unknown'  # Not proof of non-delivery.
    next_revision = asyncio.create_task(prompt(peer, sid, revision=1))
    await until(lambda: len(runtime.sent) == 2)
    await finish(service, sid, runtime.sent[-1][2])
    assert 'result' in await next_revision
    await peer.socket.close()


async def test_revoke_identity_change_stale_stop_and_cross_connection_scopes(host):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    a, _ = await connect_profile(host)
    b, _ = await connect_profile(host)
    sid = (await new(a, path, 'R'))['result']['sessionId']
    source_plan(service, service._session(sid))
    assert 'error' in await inspect_input(b, sid)  # Not attached by this profile.
    await service.dispatch('conversation.stop', {'sessionId': sid}, origin='ui',
                           command_id='owner-stop', include_state=False)
    await until(lambda: service._session(sid)['status'] == 'stopped')
    assert 'prepared before Stop' in (await prompt(a, sid))['error']['message']
    assert not runtime.sent
    config = service.server_config['agentInstance']
    config['sources'] = [room_source('S')]
    assert 'error' in await inspect_input(a, sid)
    config['sources'] = [room_source('R'), room_source('S')]
    config['id'] = 'urn:example:agent:replacement'
    assert 'error' in await prompt(a, sid, epoch=1)
    assert not runtime.sent
    await a.socket.close()
    await b.socket.close()


async def test_source_revocation_during_creation_await_cannot_bind_or_send(host, monkeypatch):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    entered, released = asyncio.Event(), asyncio.Event()
    original = runtime.bind_ephemeral_mcp

    async def waiting(*args):
        entered.set()
        await released.wait()
        return await original(*args)

    monkeypatch.setattr(runtime, 'bind_ephemeral_mcp', waiting)
    creating = asyncio.create_task(new(peer, path, 'R'))
    await entered.wait()
    service.server_config['agentInstance']['sources'] = []
    released.set()
    assert 'error' in await creating
    assert not runtime.sent and not runtime.bindings
    await peer.socket.close()


async def test_fresh_attachment_recovers_saved_binding_without_replay(host):
    configure(host)
    app, _, runtime, path = host
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    await peer.socket.close()
    await until(lambda: not app['acp_connections'])
    service = app['service']
    from amplifier_web.agent_instance import InstanceProfile
    from amplifier_web.execution_owner import ExecutionOwner
    # A fresh owner reads the persisted source binding from the same canonical
    # service record; full host-restart coverage lives with the integration owner.
    owner = ExecutionOwner(service, workspace=path, bundle='foundation', origin='ui', enable_mcp=True)
    profile = InstanceProfile(owner)
    assert (await profile.connect(str(path), session_metadata(
        'urn:example:agent:U', room_source('R'))[NAMESPACE], {}, sid))[0] == sid
    assert not runtime.sent
    await owner.disconnect()


async def test_old_input_inspection_survives_stop_and_new_intent_is_explicit(host):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    source_plan(service, service._session(sid))
    pending = asyncio.create_task(prompt(peer, sid))
    await until(lambda: len(runtime.sent) == 1)
    await finish(service, sid, runtime.sent[-1][2])
    first = (await pending)['result']['_meta'][NAMESPACE]
    await service.dispatch('conversation.stop', {'sessionId': sid},
                           origin='ui', command_id='explicit-stop', include_state=False)
    await until(lambda: service._session(sid)['status'] == 'stopped')
    old = (await inspect_input(peer, sid, epoch=1))['result']
    assert old['commandId'] == first['commandId'] and old['delivery'] == 'accepted'
    assert len(runtime.sent) == 1
    fresh = asyncio.create_task(prompt(peer, sid, identity='fresh-explicit', epoch=1))
    await until(lambda: len(runtime.sent) == 2)
    await finish(service, sid, runtime.sent[-1][2])
    assert 'result' in await fresh
    await peer.socket.close()


async def test_profile_label_unrelated_sources_and_optional_restriction(host):
    configure(host)
    app, _, runtime, path = host
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    config = app['service'].server_config['agentInstance']
    config['label'] = 'Renamed, not another agent'
    config['sources'].append(room_source('T'))
    assert 'result' in await inspect_input(peer, sid)
    del config['sources']  # Existing owner launcher can bind another room directly.
    assert 'result' in await new(peer, path, 'unlisted-room')
    assert not runtime.sent
    await peer.socket.close()


async def test_actual_task_owner_cas_consumption_stop_and_lost_receipt(tmp_path, monkeypatch):
    """Actual RuntimeControls persistence, with controlled transport, not native qualification."""
    from amplifier_web.agent_instance import InstanceProfile
    from amplifier_web.execution_owner import ExecutionOwner
    from amplifier_web.service import AppService
    from test_task_continuity import controls, create
    from test_acp_server import Runtime

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    service.server_config = {'agentInstance': {'id': 'urn:example:agent:U', 'label': 'U',
        'workspace': str(tmp_path), 'bundle': 'foundation'}}
    owner = ExecutionOwner(service, workspace=tmp_path, bundle='foundation', origin='ui', enable_mcp=True)
    profile = InstanceProfile(owner)
    binding = session_metadata('urn:example:agent:U', room_source('R'))[NAMESPACE]
    worker = None
    try:
        sid, _ = await profile.connect(str(tmp_path), binding, {})
        source_plan(service, service._session(sid))
        worker = controls(sid)
        # Use the ordinary native task owner to capture actual source provenance.
        original_input = metadata()[NAMESPACE]
        _, input_id = profile.input(sid, original_input)
        worker.runtime.generation = {'id': 'fixture-generation', 'input_ids': [input_id]}
        await create(worker, maxTurns=5)
        worker.tasks.apply()
        worker.coordinator.session_state['goal']['turns_used'] = 2
        worker.persist(task_only=True)
        initial = worker.tasks.record()
        task_id = initial['id']
        assert initial['sourceInputIds'] == [input_id]
        observed = await profile.inspect_input(sid, original_input)
        assert observed['work']['taskId'] == task_id
        assert (await profile.inspect_input(sid, metadata(identity='other')[NAMESPACE]))['work'] is None
        calls = []

        async def control(target, operation, arguments, *, guard=None):
            if guard:
                guard()
            calls.append(operation)
            result = await worker.perform(operation, arguments)
            return result

        service.runtime.control = control
        service.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock(),
                                            command=AsyncMock())
        change = {'sessionId': sid, 'binding': binding, 'operation': 'update',
                  'commandId': 'amend', 'taskId': task_id, 'expectedRevision': 1,
                  'changes': {'correction': 'Keep the original limits and results', 'maxTurns': 3}}
        changed = await profile.control(change)
        assert changed['result']['task']['id'] == task_id
        assert worker.tasks.record()['revision'] == 2
        assert worker.tasks.record()['goalState']['turns_used'] == 2
        duplicate = await profile.control(change)
        assert duplicate['result']['duplicate'] is True
        with pytest.raises(Exception, match='revision changed'):
            await profile.control({**change, 'commandId': 'stale'})
        with pytest.raises(Exception, match='replaced'):
            await profile.control({**change, 'commandId': 'foreign', 'taskId': 'another-task',
                                   'expectedRevision': 2})
        with pytest.raises(ValueError, match='Unsupported'):
            await profile.control({'sessionId': sid, 'binding': binding, 'operation': 'resume'})
        from amplifier_web.message_delivery import execution_boundary
        stopped = await profile.control({**change, 'operation': 'stop', 'commandId': 'stop',
            'expectedRevision': 2, 'changes': {}, 'expectedExecution': execution_boundary(service._session(sid))})
        assert stopped['settlement'] == 'inspect-native-state'
        await until(lambda: service._session(sid)['status'] == 'stopped')
        assert worker.tasks.record()['status'] == 'paused'
        assert worker.tasks.record()['goalState']['turns_used'] == 2
        read = await profile.control({'sessionId': sid, 'binding': binding,
                                      'operation': 'workReceipt', 'commandId': 'stop'})
        assert read['task']['receipt']['action'] == 'task.pause'
        assert read['stop']['accepted'] is True
        assert service.runtime.stopped == [sid]
        # Updating paused work is not resume; preserve status and consumed turns.
        await profile.control({**change, 'commandId': 'paused-edit', 'expectedRevision': 3,
                               'changes': {'correction': 'Clarify without restarting'}})
        assert worker.tasks.record()['status'] == 'paused'
        assert not await worker.tasks.continuation_allowed()
    finally:
        service.management = None
        if worker:
            await worker.close()
        await owner.close()


async def test_native_only_delivery_reconciliation_and_lookup_not_invented_attribution(host, monkeypatch):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    source_plan(service, service._session(sid))
    owner, = service._execution_observers.attachments
    from amplifier_web.agent_instance import InstanceProfile
    profile = InstanceProfile(owner)
    # Use the actual negotiated profile for its server-held binding.
    profile.bindings[sid] = session_metadata('urn:example:agent:U', room_source('R'))[NAMESPACE]
    profile.execution_sources[sid] = profile.execution_source(service._session(sid))
    _, command = profile.input(sid, metadata()[NAMESPACE])
    probes = []

    async def native_delivery(session, identity):
        probes.append((session['id'], identity))
        return 'accepted' if identity == command else 'unknown'

    monkeypatch.setattr(runtime, 'delivery', native_delivery, raising=False)
    altered = metadata()[NAMESPACE]
    altered['input']['actor'] = 'forged:actor'
    altered['input']['audience'] = ['private:elsewhere']
    result = await profile.inspect_input(sid, altered)
    assert result['delivery'] == 'accepted'
    assert result['input'] == {'id': 'original', 'revision': 0}
    assert result['attribution'].startswith('lookup-key-only')
    assert probes == [(sid, command)] and not runtime.sent
    await peer.socket.close()


async def test_taskless_reconnect_stop_and_delayed_stop_refuses_newer_input(host):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    source_plan(service, service._session(sid))
    control_path = service.data_dir / 'sessions' / sid / 'control-state.json'
    saved = json.loads(control_path.read_text())
    control_path.write_text(json.dumps({**saved, 'task': None}))
    pending = asyncio.create_task(prompt(peer, sid))
    await until(lambda: len(runtime.sent) == 1)
    await peer.socket.close()
    await asyncio.gather(pending, return_exceptions=True)
    await until(lambda: not app['acp_connections'])
    peer, _ = await connect_profile(host)
    await peer.call('session/resume', sessionId=sid, cwd=str(path), mcpServers=[],
        _meta=session_metadata('urn:example:agent:U', room_source('R')))
    binding = session_metadata('urn:example:agent:U', room_source('R'))[NAMESPACE]
    state_row = await peer.call('_amplifier/instance', sessionId=sid, binding=binding, operation='read')
    assert 'result' in state_row, state_row
    state = state_row['result']
    intent = {'sessionId': sid, 'binding': binding, 'operation': 'stop', 'commandId': 'taskless-stop',
              'taskId': None, 'expectedRevision': 0, 'changes': {},
              'expectedExecution': state['expectedExecution']}
    # A second source input accepted before the delayed Stop wins; old Stop
    # cannot interrupt it, even though no durable task revision changed.
    async with asyncio.timeout(5):
        await service.dispatch('conversation.send', {'sessionId': sid, 'text': 'New explicit work'},
                               origin='ui', command_id='newer', include_state=False)
    refused = await peer.call('_amplifier/instance', **intent)
    assert 'superseded' in refused['error']['message'] and not runtime.stopped
    current = (await peer.call('_amplifier/instance', sessionId=sid, binding=binding, operation='read'))['result']
    stopped = await peer.call('_amplifier/instance', **{**intent, 'commandId': 'fresh-stop',
                                                      'expectedExecution': current['expectedExecution']})
    assert stopped['result']['task'] is None
    await until(lambda: service._session(sid)['status'] == 'stopped')
    assert runtime.stopped == [sid]
    await peer.socket.close()


async def test_profile_binding_commands_survive_actual_service_reconstruction(tmp_path):
    from amplifier_web.agent_instance import InstanceProfile
    from amplifier_web.execution_owner import ExecutionOwner
    from amplifier_web.service import AppService
    from test_acp_server import Runtime

    config = {'agentInstance': {'id': 'urn:example:agent:U', 'label': 'U',
                               'workspace': str(tmp_path), 'bundle': 'foundation'}}
    runtime = Runtime()
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    service.server_config = config
    owner = ExecutionOwner(service, workspace=tmp_path, bundle='foundation', origin='ui', enable_mcp=True)
    profile = InstanceProfile(owner)
    binding = session_metadata('urn:example:agent:U', room_source('R'))[NAMESPACE]
    sid, _ = await profile.connect(str(tmp_path), binding, {})
    source_plan(service, service._session(sid))
    async def observe(row):
        pass
    pending = asyncio.create_task(profile.prompt(sid, metadata()[NAMESPACE], 'One accepted input', observe))
    await until(lambda: len(runtime.sent) == 1)
    command = runtime.sent[0][2]
    await finish(service, sid, command)
    assert (await pending)['outcome'] == 'completed'
    await owner.disconnect()
    await service.close()
    fresh_runtime = Runtime()
    fresh = AppService(tmp_path / 'app', fresh_runtime, workspace=tmp_path)
    fresh.server_config = config
    attached = ExecutionOwner(fresh, workspace=tmp_path, bundle='foundation', origin='ui', enable_mcp=True)
    recovered = InstanceProfile(attached)
    try:
        assert (await recovered.connect(str(tmp_path), binding, {}, sid))[0] == sid
        receipt = await recovered.inspect_input(sid, metadata()[NAMESPACE])
        assert receipt['commandId'] == command and receipt['delivery'] == 'accepted'
        assert not fresh_runtime.sent
    finally:
        await attached.disconnect()
        await fresh.close()


async def test_many_task_source_inputs_use_bounded_lookup_and_existing_detail(tmp_path, monkeypatch):
    from amplifier_web.task_continuity import _read
    from test_task_continuity import controls, create
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    worker.runtime.generation = {'id': 'generation', 'input_ids': ['input-' + str(i) for i in range(2000)]}
    try:
        await create(worker)
        task = worker.tasks.record()
        result = _read('task.get', {'sourceInputId': 'input-1999'}, task=task,
                       history=[], receipts={}, sid='task')
        assert result['sourceInputAssociated'] is True
        assert result['task']['sourceInputCount'] == 2000
        assert 'sourceInputIds' not in result['task']
        assert len(json.dumps(result).encode()) < 9 * 1024
        page = _read('task.detail', {'taskId': task['id'], 'expectedRevision': task['revision'],
            'field': 'sourceInputIds', 'limit': 100}, task=task, history=[], receipts={}, sid='task')
        assert page['truncated'] and page['nextOffset'] == 100
    finally:
        await worker.close()


async def test_profile_source_change_during_approval_commit_does_not_decide(host, monkeypatch):
    configure(host)
    app, _, runtime, path = host
    service = app['service']
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    source_plan(service, service._session(sid))
    pending = asyncio.create_task(prompt(peer, sid))
    await until(lambda: len(runtime.sent) == 1)
    decisions = []
    runtime.approval = AsyncMock(side_effect=lambda *args: decisions.append(args))
    peer.permission = AsyncMock(return_value={'outcome': 'selected', 'optionId': 'allow'})
    original = service.dispatch

    async def delayed(action, *args, **kwargs):
        if action == 'approval.respond':
            service.server_config['agentInstance']['sources'] = []
        return await original(action, *args, **kwargs)

    monkeypatch.setattr(service, 'dispatch', delayed)
    await service.on_runtime_event('approval.requested', {
        'sessionId': sid, 'id': 'approval', 'prompt': 'An exact current tool request',
        'options': ['allow', 'deny']})
    result = await pending
    assert 'error' in result and not decisions
    assert service._session(sid)['approvals'][0]['status'] == 'pending'
    await peer.socket.close()


async def test_changed_execution_location_is_not_the_original_source_binding(host):
    configure(host)
    app, _, runtime, path = host
    peer, _ = await connect_profile(host)
    sid = (await new(peer, path, 'R'))['result']['sessionId']
    session = app['service']._session(sid)
    session['workingDirectory'] = str(path / 'moved')
    result = await inspect_input(peer, sid)
    assert 'source or location changed' in result['error']['message']
    assert not runtime.sent
    await peer.socket.close()