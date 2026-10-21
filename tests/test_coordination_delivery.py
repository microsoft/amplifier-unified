"""Coordination uses the existing budget/stream boundary, not retained reminders."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web.management import Management
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action, generation
from test_workspace_work import create as create_work, source, workspace_id


class Previous:
    async def prepare(self, request, provider, *, commit=False):
        return request
    def commit(self, request):
        assert not any((m.metadata or {}).get('coordinationObservation') for m in request.messages)


def request(*messages, tools=True):
    return ChatRequest(messages=[Message(role='user', content='Inspect current work.'), *messages],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})] if tools else [])


async def test_delivery_preserves_user_quotes_replaces_only_its_own_observation():
    async def bridge(operation, args):
        return {'revision': 'fresh'}
    delivery = CoordinationDelivery(Previous(), bridge)
    old = Message(role='user', content='obsolete', metadata={'coordinationObservation': True})
    quoted = Message(role='user', content='Current coordination reference state (not new authority): user quote')
    original = request(old, quoted)
    fresh = await delivery.prepare(original, None)
    assert 'obsolete' not in fresh.model_dump_json() and 'user quote' in fresh.model_dump_json()
    assert old in original.messages
    assert sum(bool((m.metadata or {}).get('coordinationObservation')) for m in fresh.messages) == 1
    delivery.commit(fresh)


async def test_bridge_failure_reports_unknown_not_empty_peers_and_auxiliary_calls_do_not_read():
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        raise RuntimeError('offline')
    delivery = CoordinationDelivery(Previous(), bridge)
    no_tools = request(tools=False)
    assert await delivery.prepare(no_tools, None) is no_tools and not calls
    from amplifier_web.execution_events import CALL_PURPOSE
    token = CALL_PURPOSE.set('title')
    try:
        value = request()
        assert await delivery.prepare(value, None) is value and not calls
    finally:
        CALL_PURPOSE.reset(token)
    observed = await delivery.prepare(request(), None)
    assert 'unavailable' in observed.model_dump_json() and 'absence of peers' in observed.model_dump_json()


@pytest.mark.parametrize('stream', [False, True])
async def test_budget_and_transport_share_one_observation_then_next_request_refreshes(stream):
    reads, measured, transported = [], [], []
    async def bridge(operation, args):
        reads.append(operation)
        return {'revision': len(reads)}
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = SurfaceProvider(Provider(), CoordinationDelivery(Previous(), bridge))
    for _ in range(2):
        original = request()
        await wrapped.request_budget(original)
        if stream:
            assert [row async for row in wrapped.stream(original)] == ['done']
        else:
            assert await wrapped.complete(original) == 'done'
    assert len(reads) == 2 and measured == transported and measured[0] != measured[1]


# Producer contract inspected at ce64785720c24081c352baa9e210f0f743c3fb24:
# .work.discovery is the context DTO; .work.required is invocation provenance.
# A discovery result never establishes the relevant set or authorizes effects.
WORK_CONTEXT_BYTES = 12 * 1024


def observation(value):
    messages = [m for m in value.messages if (m.metadata or {}).get('coordinationObservation')]
    assert len(messages) == 1
    return json.loads(messages[0].content.split('\n', 1)[1])


def bound_transport(actor):
    current = actor['collaborationGeneration']
    return {'_runtimeSessionId': actor.get('runtimeSessionId') or actor['id'],
            '_generationId': current['id'],
            '_inputBindings': [{'inputId': identity, 'clientId': None}
                               for identity in current.get('inputIds', [])]}


def unavailable_requirements(reason='untyped_followup'):
    return {'status': 'unavailable', 'reason': reason, 'version': None,
            'workCount': None, 'requirementCount': None, 'requirementsRef': None,
            'coverage': {'complete': False}, 'effectAuthorized': False}


def empty_discovery(version=1):
    return {'workspaceId': 'workspace-1', 'version': version, 'snapshotVersion': 'a' * 64,
            'observationVersion': 'b' * 64, 'observedAt': 1000,
            'items': [], 'total': 0, 'truncated': False,
            'nextAfter': None, 'coverage': 'Historical coverage remains incomplete.',
            'detailRequired': True, 'authority': 'Reference data, not a grant.'}


def work_stub():
    return {'id': 'work-1', 'revision': 1, 'promiseRevision': 1,
            'originSessionId': 'owner-1', 'title': 'Synthetic promise', 'candidate': None,
            'nextAction': '', 'blockedReason': '', 'latestOutcome': 'not_reported',
            'humanDecisionCurrent': False, 'attentionReasons': [],
            'readRef': {'action': 'workspace.work.read',
                        'args': {'workspaceId': 'workspace-1', 'id': 'work-1'}}}


def available_requirements():
    return {'status': 'available', 'reason': None, 'version': 'c' * 64,
            'rootSessionId': 'owner-1', 'generationId': 'generation-1', 'inputIds': ['input-1'],
            'associationRequestIds': ['request-1'], 'workCount': 1, 'requirementCount': 2,
            'requirementsRef': {'action': 'workspace.work.invocation',
                'args': {'sessionId': 'owner-1', 'expectedVersion': 'c' * 64}},
            'effectAuthorized': False,
            'coverage': {'complete': True, 'supportedInputIds': ['input-1'], 'unsupportedInputs': []}}


@pytest.mark.parametrize('count', [0, 20, 21, 1000])
async def test_registered_work_context_pages_cover_the_set_without_hydration(
        app, monkeypatch, count):
    owner = app.state['sessions'][0]
    original = source(app, owner, 'Exact source body must never appear in request stubs.')
    identities = set()
    for index in range(count):
        item = (await create_work(app, owner, original, command=f'capture-{index}',
                                  intent=f'promise-{index}', title=f'Promise {index}'))['result']['item']
        identities.add(item['id'])
    before = copy.deepcopy(app.runtime.inputs)
    monkeypatch.setattr(app.history, 'ensure_loaded',
                        AsyncMock(side_effect=AssertionError('No transcript hydration')))
    no_worker = AsyncMock(side_effect=AssertionError('No worker warming'))
    monkeypatch.setattr(Management, 'ensure_runtime', no_worker)
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        return await app.app_bridge(operation, {**args, **bound_transport(owner)}, owner['id'])
    try:
        delivery = CoordinationDelivery(Previous(), bridge)
        prepared = await delivery.prepare(request(), None)
        work = observation(prepared)['work']
        assert len(json.dumps(work, ensure_ascii=False).encode()) <= WORK_CONTEXT_BYTES
        assert work['available'] is True
        # These ordinary fixture inputs have no typed commissioning association.
        # Even zero discovered work cannot assert complete invocation coverage.
        assert work['required']['status'] == 'unavailable'
        assert work['required']['effectAuthorized'] is False
        assert work['required']['coverage']['complete'] is False
        page = work['discovery']
        assert calls == ['coordination.current']  # One request-boundary observation.
        seen, cursors = set(), set()
        for _ in range(1001):
            assert len(json.dumps(page, ensure_ascii=False).encode()) <= WORK_CONTEXT_BYTES
            assert len(page['items']) <= 20
            assert page['total'] == count
            assert page['coverage']  # Recorded empty is not historical completeness.
            assert 'Exact source body' not in json.dumps(page)
            batch = {row['id'] for row in page['items']}
            assert not seen.intersection(batch)
            seen.update(batch)
            assert page['truncated'] == bool(page['nextAfter'])
            if page['nextAfter'] is None:
                break
            assert batch and page['nextAfter'] not in cursors
            cursors.add(page['nextAfter'])
            page = (await app.app_bridge('dispatch', {
                'action': 'workspace.work.context',
                'args': {'sessionId': owner['id'], 'after': page['nextAfter']},
                **bound_transport(owner)}, owner['id']))['result']
        else:
            pytest.fail('Work context paging did not terminate')
        assert seen == identities
        assert app.runtime.inputs == before
        no_worker.assert_not_called()
        delivery.commit(prepared)
    finally:
        assert app.runtime.inputs == before


async def test_work_context_includes_owned_and_contributing_items_only(app):
    owner, contributor = app.state['sessions']
    own = (await create_work(app, owner))['result']['item']
    await generation(app, contributor, 'contributor-generation', ['synthetic-input'])
    linked = (await create_work(app, contributor, command='contributed',
                               intent='contributed'))['result']['item']
    unrelated = (await create_work(app, contributor, command='unrelated',
                                  intent='unrelated'))['result']['item']
    await agent_action(app, contributor, 'workspace.work.link', {
        'sessionId': contributor['id'], 'workspaceId': workspace_id(app, contributor),
        'id': linked['id'], 'expectedRevision': linked['revision'],
        'linkedSessionId': owner['id'], 'role': 'reviewer', 'candidate': 'a' * 40,
    }, 'link-reviewer')
    value = await app.app_bridge('coordination.current', bound_transport(owner), owner['id'])
    ids = {row['id'] for row in value['work']['discovery']['items']}
    assert ids == {own['id'], linked['id']}
    assert unrelated['id'] not in ids
    assert value['work']['required']['status'] == 'unavailable'
    assert value['work']['required']['effectAuthorized'] is False


async def test_work_cursor_rejects_a_changed_set_instead_of_mixing_snapshots(app):
    owner = app.state['sessions'][0]
    original = source(app, owner)
    for index in range(21):
        await create_work(app, owner, original, command=f'initial-{index}', intent=str(index))
    first = (await app.app_bridge('dispatch', {
        'action': 'workspace.work.context', 'args': {'sessionId': owner['id']}}, owner['id']))['result']
    assert first['nextAfter']
    await create_work(app, owner, original, command='new-record', intent='new-record')
    with pytest.raises(AppError, match='changed|refresh|snapshot'):
        await app.app_bridge('dispatch', {'action': 'workspace.work.context',
            'args': {'sessionId': owner['id'], 'after': first['nextAfter']}}, owner['id'])


@pytest.mark.parametrize('work', [
    None,
    {'available': False, 'discovery': {'status': 'unavailable', 'reason': 'Projection failed'},
     'required': unavailable_requirements('Projection failed')},
])
async def test_missing_work_projection_is_not_an_empty_complete_set(work):
    async def bridge(operation, args):
        return {} if work is None else {'work': work}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    projected = observation(prepared)['work']
    assert projected['available'] is False
    assert projected['required']['status'] == 'unavailable'
    assert projected['required']['coverage']['complete'] is False
    assert projected['required']['effectAuthorized'] is False
    assert projected['discovery'].get('total') is None


async def test_unbounded_or_body_bearing_work_projection_fails_closed():
    async def bridge(operation, args):
        return {'work': {'available': True, 'discovery': {'items': [{
            'id': 'work-one', 'revision': 1, 'promiseRevision': 1,
            'constraints': ['PRIVATE_CONSTRAINT_BODY' * 20000],
            'originalSource': {'text': 'PRIVATE_SOURCE_BODY' * 20000},
        }], 'total': 1, 'coverage': 'unknown'}, 'required': unavailable_requirements()}}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    text = prepared.model_dump_json()
    assert 'PRIVATE_CONSTRAINT_BODY' not in text
    assert 'PRIVATE_SOURCE_BODY' not in text
    projected = observation(prepared)['work']
    assert projected['available'] is False
    assert projected['required']['status'] == 'unavailable'
    assert projected['required']['effectAuthorized'] is False
    assert projected['discovery'].get('total') is None  # Failed projection is not trusted.


@pytest.mark.parametrize('change', ['small_body', 'claimed_grant', 'false_complete'])
async def test_work_projection_rejects_invalid_semantics_even_below_byte_limit(change):
    work = {'available': True, 'discovery': empty_discovery(),
            'required': unavailable_requirements()}
    if change == 'small_body':
        work['discovery']['total'] = 1
        work['discovery']['items'] = [{
            'id': 'work-1', 'revision': 1, 'promiseRevision': 1,
            'originalSource': {'text': 'SOURCE_BODY_SHOULD_NOT_BE_IN_PREFIX'},
            'readRef': {'action': 'workspace.work.read',
                        'args': {'workspaceId': 'workspace-1', 'id': 'work-1'}}}]
    elif change == 'claimed_grant':
        work['required']['effectAuthorized'] = True
    else:
        work['required']['coverage']['complete'] = True
    assert len(json.dumps(work).encode()) < WORK_CONTEXT_BYTES
    async def bridge(operation, args):
        return {'work': work}
    value = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    result = observation(value)['work']
    assert result['available'] is False
    assert result['required']['coverage']['complete'] is False
    assert result['required']['effectAuthorized'] is False
    assert 'SOURCE_BODY_SHOULD_NOT_BE_IN_PREFIX' not in value.model_dump_json()


@pytest.mark.parametrize('change', [
    'missing_header', 'missing_id', 'wrong_ref', 'nested_title', 'nested_candidate',
    'bad_revision', 'wrong_coverage', 'wrong_requirements_ref',
])
async def test_work_dto_shape_and_read_handles_are_required_below_byte_bound(change):
    from amplifier_web.coordination_delivery import _work_reference
    work = {'available': True, 'discovery': {**empty_discovery(), 'items': [work_stub()], 'total': 1},
            'required': available_requirements()}
    # The control is valid, so a rejection cannot be due to a broken fixture.
    assert _work_reference(work) == work
    row, header = work['discovery']['items'][0], work['required']
    if change == 'missing_header':
        work['required'] = {'status': 'available', 'version': 'v',
                            'effectAuthorized': False, 'coverage': {'complete': True}}
    elif change == 'missing_id':
        row.pop('id')
    elif change == 'wrong_ref':
        row['readRef']['args']['id'] = 'other-work'
    elif change in {'nested_title', 'nested_candidate'}:
        row[change.removeprefix('nested_')] = {'body': 'HIDDEN_SOURCE_BODY'}
    elif change == 'bad_revision':
        row['promiseRevision'] = True
    elif change == 'wrong_coverage':
        header['coverage']['supportedInputIds'] = ['other-input']
    else:
        header['requirementsRef']['args']['expectedVersion'] = 'd' * 64
    assert len(json.dumps(work).encode()) < WORK_CONTEXT_BYTES
    async def bridge(operation, args):
        return {'work': work}
    prepared = await CoordinationDelivery(Previous(), bridge).prepare(request(), None)
    result = observation(prepared)['work']
    assert result['available'] is False
    assert result['required']['coverage']['complete'] is False
    assert result['required']['effectAuthorized'] is False
    assert 'HIDDEN_SOURCE_BODY' not in prepared.model_dump_json()


@pytest.mark.parametrize('stream', [False, True])
async def test_work_observation_is_measured_and_transported_once_per_request(stream):
    calls, measured, transported = [], [], []
    async def bridge(operation, args):
        calls.append(operation)
        return {'work': {'available': True, 'discovery': empty_discovery(len(calls)),
                         'required': unavailable_requirements()}}
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = SurfaceProvider(Provider(), CoordinationDelivery(Previous(), bridge))
    for _ in range(2):
        original = request()
        await wrapped.request_budget(original)
        if stream:
            assert [row async for row in wrapped.stream(original)] == ['done']
        else:
            assert await wrapped.complete(original) == 'done'
    assert calls == ['coordination.current', 'coordination.current']
    assert measured == transported
    assert observation_json_version(measured[0]) != observation_json_version(measured[1])


def observation_json_version(serialized):
    return observation(ChatRequest.model_validate_json(serialized))['work']['discovery']['version']


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('stale', [False, True])
async def test_outer_delivery_preserves_inner_final_boundary(stream, stale):
    """Exercise the real wrapper chain, not a production authority predicate."""
    from amplifier_web.surface_delivery import SurfaceDelivery
    from amplifier_web.voice_visual_delivery import VoiceVisualDelivery
    from amplifier_web.output_image_delivery import OutputImageDelivery
    from amplifier_web.memory_delivery import MemoryDelivery

    reads, events, measured, transported = [], [], [], []
    replies = {
        'context.manifest': {'inputIds': ['synthetic-input'], 'surfaces': []},
        'coordination.current': {
            'work': {'available': True, 'discovery': empty_discovery(),
                     'required': unavailable_requirements()}},
        'memory.context': {'items': []},
    }

    async def bridge(operation, args):
        reads.append((operation, copy.deepcopy(args)))
        return copy.deepcopy(replies[operation])

    class ChangedAtBoundary(RuntimeError):
        pass

    class CheckedCoordination(CoordinationDelivery):
        async def revalidate(self, value):
            events.append('revalidate')
            assert value.model_dump_json().encode('utf-8') == measured[0]
            if self.stale:
                raise ChangedAtBoundary('changed after budget')
            return value

        def commit(self, value):
            events.append('commit')
            super().commit(value)

    class Provider:
        async def request_budget(self, value, **kwargs):
            events.append('budget')
            measured.append(value.model_dump_json().encode('utf-8'))
            return {'tokens': 100}

        async def complete(self, value, **kwargs):
            events.append('transport')
            transported.append(value.model_dump_json().encode('utf-8'))
            return 'done'

        async def stream(self, value, **kwargs):
            events.append('transport')
            transported.append(value.model_dump_json().encode('utf-8'))
            yield 'done'

    inner = CheckedCoordination(SurfaceDelivery(bridge), bridge)
    inner.stale = False
    delivery = MemoryDelivery(
        OutputImageDelivery(VoiceVisualDelivery(inner, bridge), bridge), bridge)
    wrapped = SurfaceProvider(Provider(), delivery)
    original = request(Message(role='user', content='Preserve \u03bb and caf\u00e9.'))
    original_bytes = original.model_dump_json().encode('utf-8')
    await wrapped.request_budget(original)
    assert events == ['budget']
    inner.stale = stale  # Mutate only after fitting, before transport.

    async def send():
        if stream:
            return [row async for row in wrapped.stream(original)]
        return [await wrapped.complete(original)]

    if stale:
        with pytest.raises(ChangedAtBoundary, match='changed after budget'):
            await send()
        assert transported == []
        assert events == ['budget', 'revalidate']
    else:
        assert await send() == ['done']
        assert transported == measured
        assert events == ['budget', 'revalidate', 'commit', 'transport']
    assert len(measured) == 1
    assert reads == [('context.manifest', {}), ('coordination.current', {}),
                     ('memory.context', {})]
    assert original.model_dump_json().encode('utf-8') == original_bytes


class InstalledWorkAdapter:
    """Real mounted app tool + real loop tool dispatch, no model service.

    This is host-request coverage, not native transport or effect admission.
    """
    async def setup(self, app, child):
        from types import SimpleNamespace
        from amplifier_core import HookRegistry
        from amplifier_web.app_guidance import install_app_access
        from amplifier_module_loop_live import BundleLiveOrchestrator
        from test_work_invocation import transport
        self.tools, self.capabilities, self.messages = {}, {}, []
        self.hooks = HookRegistry()
        self.loop = BundleLiveOrchestrator({})
        self.loop._tool_calls_this_turn = 0
        self.counter = 0
        async def mount(kind, tool, name):
            self.tools[name] = tool
        coordinator = SimpleNamespace(get_capability=self.capabilities.get,
            get=lambda kind: self.tools if kind == 'tools' else None,
            register_capability=self.capabilities.__setitem__, mount=mount, hooks=self.hooks)
        async def bridge(operation, args):
            if operation == 'context.manifest':
                return {'surfaces': [], 'inputIds': child['collaborationGeneration']['inputIds']}
            if operation == 'memory.context':
                return {'items': []}
            return await app.app_bridge(operation, {**args, **transport(child)}, child['id'])
        await install_app_access(coordinator, bridge)
        self.delivery = self.capabilities['web.surface_delivery']
        return self

    def request(self):
        return request(*self.messages)

    async def read(self, ref, **patch):
        from amplifier_core.message_models import ToolCall
        self.counter += 1
        args = {'operation': 'dispatch', 'args': {
            'action': ref['action'], 'args': {**ref['args'], **patch}}}
        call = ToolCall(id=f'work-read-{self.counter}', name='app_control', arguments=args)
        self.messages.append(Message(role='assistant', content='', tool_calls=[{
            'id': call.id, 'tool': call.name, 'arguments': call.arguments}]))
        identity, name, content = await self.loop._execute_tool_only(
            call, self.tools, self.hooks, None)
        self.messages.append(Message(role='tool', name=name, tool_call_id=identity, content=content))
        from amplifier_web.output_image_delivery import successful_output
        return successful_output(content)['result']

    async def fill(self, required):
        rows, cursor = [], None
        while True:
            page = await self.read(required['requirementsRef'], limit=1,
                                   **({'after': cursor} if cursor else {}))
            rows.extend(page['items'])
            cursor = page['nextAfter']
            if cursor is None:
                break
        for row in rows:
            offset = 0
            while True:
                value = await self.read(row['readRef'], offset=offset, limit=700)
                page = value['source'] if row['kind'] == 'source' else value
                offset = page['nextOffset']
                if offset is None:
                    break


async def installed_work_adapter(app):
    from test_work_invocation import prepared_work, commissioned, current_work
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'delivered-pages', constraints=['Preserve \u03bb caf\u00e9. ' * 250])
    _, child = await commissioned(app, owner, item)
    required = (await current_work(app, child))['required']
    assert required['status'] == 'available'
    return await InstalledWorkAdapter().setup(app, child), child, required


@pytest.mark.parametrize('fault', ['none', 'partial', 'forged_id', 'changed_body', 'compacted', 'same_call', 'reopened'])
async def test_installed_adapter_requires_genuine_complete_retained_pages(app, fault):
    adapter, child, required = await installed_work_adapter(app)
    before = await adapter.delivery.prepare(adapter.request(), None)
    assert adapter.delivery.request_coverage(before)['complete'] is False
    await adapter.fill(required)
    assert adapter.counter >= 4  # Manifest and Unicode content paging both occurred.
    # A read occurring after a request was fitted cannot acknowledge that request.
    if fault == 'same_call':
        prepared = before
    else:
        if fault == 'partial':
            adapter.messages.pop()
        elif fault == 'forged_id':
            tool = next(m for m in adapter.messages if m.role == 'tool')
            tool.tool_call_id = 'forged-result-id'
        elif fault == 'changed_body':
            tool = next(m for m in reversed(adapter.messages) if m.role == 'tool')
            tool.content = tool.content[:100] + ' [truncated]'
        elif fault == 'compacted':
            adapter.messages = [Message(role='user', content='Summary claims all details were read.')]
        elif fault == 'reopened':
            old_messages = adapter.messages
            adapter = await InstalledWorkAdapter().setup(app, child)
            adapter.messages = old_messages  # History without genuine local observations.
        prepared = await adapter.delivery.prepare(adapter.request(), None)
    coverage = adapter.delivery.request_coverage(prepared)
    assert coverage['complete'] is (fault == 'none'), coverage
    assert coverage['effectAuthorized'] is False  # Not native/effect acceptance.
    if fault == 'none':
        assert coverage['coveredCount'] == required['requirementCount']
        assert coverage['scope'] == 'fitted_host_request_only'


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('change', ['none', 'stop', 'outer_await', 'changed_bytes'])
async def test_installed_work_final_boundary_checks_after_outer_awaits(app, stream, change):
    adapter, child, required = await installed_work_adapter(app)
    await adapter.fill(required)
    measured, transported = [], []
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    original = adapter.request()
    await wrapped.request_budget(original)
    async def stop():
        # Explicit fault injection at the producer's real root authority fields,
        # not a fabricated successful guard/observation receipt.
        child['status'] = 'stopped'
        child['interruptionRevision'] = child.get('interruptionRevision', 0) + 1
    if change == 'stop':
        await stop()
    elif change in {'outer_await', 'changed_bytes'}:
        previous = adapter.delivery.revalidate
        async def outer(value):
            value = await previous(value)
            if change == 'outer_await':
                await stop()
            else:
                value = value.model_copy(update={'messages': [*value.messages,
                    Message(role='user', content='Unmeasured substitution')]})
            return value
        adapter.delivery.revalidate = outer
    async def send():
        if stream:
            return [row async for row in wrapped.stream(original)]
        return [await wrapped.complete(original)]
    if change == 'none':
        assert await send() == ['done']
        assert transported == measured
    else:
        with pytest.raises(ValueError, match='refit'):
            await send()
        assert transported == []
