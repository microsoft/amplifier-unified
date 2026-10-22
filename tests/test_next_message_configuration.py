"""The public configuration path must work with no conversation runtime."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.management import Management
from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.management = Management(service)
    await service.dispatch('session.create', {'bundle': 'anchors'})
    yield service
    await service.close()


def choices(app, revision=0, **patch):
    return {'sessionId': app._session()['id'], 'expectedRevision': revision, **patch}


def retired_configuration(app, monkeypatch):
    """Retire real durable bodies, not a hot dict with hydration mocked."""
    from amplifier_web.cold_display import ColdRecord
    row = app._new_session({'bundle': 'anchors', 'title': 'Retired configuration'})
    row.update(messages=[{'id': f'saved-{n}', 'role': 'user' if n % 2 else 'assistant',
                          'text': f'Exact retained {n} ' + 'x' * 1000} for n in range(80)],
               configuration={'plan': {'context': 'retained-' + 'x' * 20000}},
               execution={'nodes': [{'id': f'tool-{n}', 'kind': 'tool', 'output': 'x' * 1000}
                                    for n in range(80)], 'turns': []},
               selection={'instance': 'one', 'model': 'chosen', 'effort': 'high'})
    app.state['sessions'].append(row)
    app.state.setdefault('runtimeControl', {})[row['id']] = {
        'configuration.inspect': {'plan': 'x' * 20000}}
    app._publish()
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    assert row['id'] in app.cold_display.retire(force=True)
    assert isinstance(row, ColdRecord)
    assert {'messages', 'execution', 'configuration'} <= set(dict.get(row, '_coldFields'))
    assert not {'messages', 'execution', 'configuration'} & set(dict.keys(row))
    return row


@pytest.mark.parametrize('operation', ['direct-save', 'public-configure', 'preview'])
async def test_retired_save_and_preview_never_load_cold_bodies(app, monkeypatch, operation):
    from amplifier_web import cold_display, draft_defaults
    from amplifier_web.next_message_configuration import save
    from amplifier_web.bundle_actions import preview_saved
    row = retired_configuration(app, monkeypatch)
    refs = deepcopy(dict.get(row, '_coldFields'))
    control = app.state['runtimeControl'][row['id']]
    control_refs = deepcopy(dict.get(control, '_coldFields'))
    request = {'sessionId': row['id'], 'expectedRevision': 0, 'bundle': 'work'}
    async def resolve(*args, **kwargs):
        assert args[1:3] == (row['workspace'], 'work')
        assert kwargs['replacement']['selection'] == row['selection']
        return {'bundle': 'work', 'fingerprint': 'saved-plan', 'modelCompatible': True}
    with monkeypatch.context() as guard:
        guard.setattr(cold_display, 'load', lambda *a: pytest.fail('Cold display body hydration'))
        guard.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('Canonical hydration')))
        guard.setattr(app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('Configuration hydration')))
        guard.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('Worker startup')))
        guard.setattr(draft_defaults, 'resolve_defaults', resolve)
        if operation == 'direct-save':
            async with app.lock:
                result = save(app, request)
            assert result['revision'] == 1
        elif operation == 'public-configure':
            receipt = await app.dispatch('conversation.configure', request, include_state=False,
                                         command_id='retired-save')
            assert receipt['result']['revision'] == 1
        else:
            await preview_saved(app.management, request)
            assert row['bundlePreview']['baseline'] == 'saved'
        assert dict.get(row, '_coldFields') == refs
        assert dict.get(control, '_coldFields') == control_refs
        assert not {'messages', 'execution', 'configuration'} & set(dict.keys(row))
        assert app.runtime is None


async def test_saved_choices_do_not_hydrate_prepare_or_mount(app, monkeypatch):
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('canonical hydration')))
    monkeypatch.setattr(app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('runtime refresh')))
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('worker start')))
    before = deepcopy(app._session()['messages'])
    selection = {'instance': 'one', 'model': 'chosen', 'effort': 'high'}
    result = await app.dispatch('conversation.configure', choices(app, bundle='work', selection=selection), command_id='save-choice')
    session = app._session()
    assert result['result']['revision'] == 1
    assert session['bundle'] == 'anchors'  # Saved intent is not the mounted root.
    assert session['pendingBundleSelection']['bundle'] == 'work'
    assert session['pendingModelSelection'] == selection
    assert session['messages'] == before and app.runtime is None
    duplicate = await app.dispatch('conversation.configure', choices(app, bundle='work', selection=selection), command_id='save-choice')
    assert duplicate['duplicate'] is True
    assert session['nextMessageRevision'] == 1
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('conversation.configure', choices(app, bundle='anchors'), command_id='stale-browser')
    assert session['pendingBundleSelection']['bundle'] == 'work'
    home, workspace, sid = app.data_dir, session['workspace'], session['id']
    await app.close()
    restored = AppService(home, workspace=workspace)
    try:
        saved = restored._session(sid)
        assert saved['nextMessageRevision'] == 1
        assert saved['pendingBundleSelection']['bundle'] == 'work'
        assert saved['pendingModelSelection'] == selection
    finally:
        await restored.close()


async def test_preparation_does_not_disable_saved_choices_but_execution_does(app):
    session = app._session()
    session.update(status='starting', preparation={'status': 'preparing'})
    await app.dispatch('conversation.configure', choices(app, bundle='work'))
    session['status'] = 'working'
    with pytest.raises(AppError, match='active work'):
        await app.dispatch('conversation.configure', choices(app, 1, bundle='anchors'))
    session['status'] = 'idle'
    session['pendingConfiguration'] = {'phase': 'error', 'effect': 'unknown'}
    with pytest.raises(AppError, match='configuration'):
        await app.dispatch('conversation.configure', choices(app, 1, bundle='anchors'))
    assert session['pendingBundleSelection']['bundle'] == 'work'


async def test_model_catalog_skips_execution_preflight_and_does_not_wait_for_resolver(app, monkeypatch):
    from amplifier_web import draft_defaults
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'providers': [], 'catalogs': {}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('canonical hydration')))
    monkeypatch.setattr(app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('runtime refresh')))
    sid = app._session()['id']
    await app.dispatch('runtime.control', {'sessionId': sid, 'operation': 'configuration.catalog'})
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(app.dispatch('session.rename', {'id': sid, 'title': 'Manual name'}), 2)
    await asyncio.wait_for(app.dispatch('conversation.configure', choices(app, bundle='work')), 2)
    assert app._session()['title'] == 'Manual name'
    assert app.runtime is None
    release.set()
    await asyncio.gather(*app.management.conversation_catalog_requests.values())


async def test_delayed_catalog_cannot_overwrite_new_bundle_revision(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.conversation_models import browse
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'providers': [{'id': 'obsolete'}], 'catalogs': {}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    task = asyncio.create_task(browse(app.management, deepcopy(app._session())))
    await entered.wait()
    await app.dispatch('conversation.configure', choices(app, bundle='work'))
    release.set()
    await task
    assert 'configuration.catalog' not in app.state.get('runtimeControl', {}).get(app._session()['id'], {})


async def test_preview_and_catalog_do_not_wait_on_runtime_mutation(app, monkeypatch):
    from amplifier_web import draft_defaults
    async def resolve(*args, **kwargs):
        return {'bundle': 'work', 'fingerprint': 'fixture', 'selection': None,
                'modelCompatible': True, 'changes': {}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('history')))
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('worker')))
    await app.management.lock.acquire()
    try:
        await app.dispatch('bundle.preview', {'sessionId': app._session()['id'], 'bundle': 'work'})
        async with asyncio.timeout(2):
            while app._session().get('bundleChange', {}).get('phase') != 'ready':
                await asyncio.sleep(.01)
        assert app._session()['bundlePreview']['baseline'] == 'saved'
        await asyncio.wait_for(app.management.list_bundles({}, 'passive-catalog'), 2)
    finally:
        app.management.lock.release()
    assert app.runtime is None


def valid_catalog():
    return {'providers': [{'id': 'one', 'sharedCatalogKey': 'one'}], 'catalogs': {'one': {
        'phase': 'ready', 'models': [{'id': 'chosen'}],
        'metadata': {'configSchema': {'fields': [{'id': 'reasoning_effort', 'choices': ['low', 'high']}]}}}}}


@pytest.mark.parametrize('fault', ['offline', 'model', 'effort', 'provider'])
def test_invalid_choices_never_pass_validation(fault):
    from amplifier_web.next_message_configuration import validate_selection
    catalog = valid_catalog()
    selected = {'instance': 'one', 'model': 'chosen', 'effort': 'high'}
    if fault == 'offline':
        catalog['catalogs']['one']['phase'] = 'error'
    else:
        selected[{'model': 'model', 'effort': 'effort', 'provider': 'instance'}[fault]] = 'unavailable'
    with pytest.raises(ValueError):
        validate_selection(selected, catalog)


async def test_genuine_input_consumes_latest_revision_and_no_older_ack(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.next_message_configuration import bind, applied
    runtime = RuntimeManager()
    app.runtime = runtime
    bind(app, runtime)
    calls = []
    async def resolve(*args, **kwargs):
        if kwargs.get('replacement'):
            return {'bundle': args[2], 'fingerprint': 'plan', 'selection': kwargs['replacement']['selection'],
                    'modelCompatible': True}
        return valid_catalog()
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    async def start(session, emit, **kwargs):
        kwargs['guard']()
        calls.append(('start', deepcopy(session)))
    async def control(sid, operation, value, **kwargs):
        kwargs['guard']()
        calls.append((operation, value))
    async def request(sid, op, **kwargs):
        kwargs['guard']()
        calls.append((op, kwargs['input_id']))
        return {'accepted': True}
    monkeypatch.setattr(runtime, 'start', start)
    monkeypatch.setattr(runtime, 'control', control)
    monkeypatch.setattr(runtime, '_request', request)
    await app.dispatch('conversation.configure', choices(app, bundle='work',
        selection={'instance': 'one', 'model': 'chosen', 'effort': 'high'}))
    await app.dispatch('conversation.configure', choices(app, 1,
        selection={'instance': 'one', 'model': 'chosen', 'effort': 'low'}))
    current = app._session()
    applied(current, {'revision': 1, 'bundle': 'work'})
    assert current['pendingModelSelection']['effort'] == 'low'
    await runtime.send(deepcopy(current), 'real synthetic input', 'one-input', app.on_runtime_event)
    assert [row[0] for row in calls] == ['start', 'provider.select', 'send']
    assert calls[0][1]['bundleReplacement']['deferred'] is True
    assert calls[1][1]['effort'] == 'low'
    assert current['bundle'] == 'work' and current['appliedConfigurationRevision'] == 2
    assert 'pendingModelSelection' not in current and 'pendingBundleSelection' not in current


@pytest.mark.parametrize('fault', ['stop', 'newer', 'permission'])
async def test_delayed_validation_refuses_before_start_and_keeps_intent(app, monkeypatch, fault):
    from amplifier_web import draft_defaults
    from amplifier_web.next_message_configuration import prepare
    from amplifier_web.runtime import RuntimeStartupError
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'bundle': 'work', 'fingerprint': 'plan', 'selection': None, 'modelCompatible': True}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    await app.dispatch('conversation.configure', choices(app, bundle='work'))
    original = deepcopy(app._session())
    attempt = asyncio.create_task(prepare(app, original))
    await entered.wait()
    if fault == 'stop':
        app._session()['interruptionRevision'] = 1
    elif fault == 'newer':
        await app.dispatch('conversation.configure', choices(app, 1, bundle='other'))
    else:
        app._session()['fileAccessPending'] = True
    release.set()
    with pytest.raises(RuntimeStartupError):
        await attempt
    assert app.runtime is None
    assert app._session()['pendingBundleSelection']['bundle'] == ('other' if fault == 'newer' else 'work')
    assert app._session()['bundle'] == 'anchors'


async def test_reset_then_explicit_pin_does_not_silently_discard_pin(app):
    await app.dispatch('conversation.configure', choices(app, bundle='work', resetModel=True))
    selected = {'instance': 'one', 'model': 'chosen'}
    await app.dispatch('conversation.configure', choices(app, 1, selection=selected))
    assert app._session()['pendingBundleSelection']['resetModel'] is False
    assert app._session()['pendingModelSelection'] == selected


async def test_immediate_switch_cannot_override_unapplied_bundle(app, monkeypatch):
    from amplifier_web.bundle_actions import perform
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('worker')))
    await app.dispatch('conversation.configure', choices(app, bundle='work'))
    with pytest.raises(ValueError, match='Next message'):
        await perform(app.management, 'bundle.switch', {'sessionId': app._session()['id'], 'bundle': 'other'})
    assert app._session()['pendingBundleSelection']['bundle'] == 'work'
    assert not app._session().get('configurationBusy')


async def test_stale_preview_settles_only_its_own_operation(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.bundle_actions import preview_saved
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'bundle': 'work', 'modelCompatible': True}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    task = asyncio.create_task(preview_saved(app.management, {'sessionId': app._session()['id'], 'bundle': 'work'}))
    await entered.wait()
    await app.dispatch('conversation.configure', choices(app, bundle='other'))
    release.set()
    await task
    assert app._session()['bundleChange']['phase'] == 'stale'
    assert 'bundlePreview' not in app._session()


async def test_unsupported_model_listing_stays_unsupported_after_cached_browse(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.conversation_models import browse
    from amplifier_web.provider_catalog import model_key
    from amplifier_web.next_message_configuration import validate_selection
    result = valid_catalog()
    result['providers'][0]['info'] = {'defaults': {'model': 'chosen'}}
    result['catalogs']['one'].update(supported=False, models=[])
    resolver = AsyncMock(return_value=result)
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolver)
    await browse(app.management, deepcopy(app._session()))
    await browse(app.management, deepcopy(app._session()))
    assert resolver.await_count == 1
    cached = app.management.provider_catalog.peek(model_key('one'))
    assert cached['modelsSupported'] is False
    validate_selection({'instance': 'one', 'model': 'chosen'}, {
        'providers': result['providers'],
        'catalogs': {'one': {'phase': 'ready', 'supported': cached['modelsSupported'],
                             'models': cached['models'], 'metadata': cached['providerMetadata']}}})


async def test_incompatible_bundle_keeps_explicit_default_recovery(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.next_message_configuration import prepare
    from amplifier_web.runtime import RuntimeStartupError
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', AsyncMock(return_value={'modelCompatible': False}))
    await app.dispatch('conversation.configure', choices(app, bundle='work',
        selection={'instance': 'one', 'model': 'chosen'}))
    with pytest.raises(RuntimeStartupError):
        await prepare(app, deepcopy(app._session()))
    assert app._session()['nextMessageFailure'] == {'code': 'incompatible-model', 'bundle': 'work'}
    await app.dispatch('conversation.configure', choices(app, 1, bundle='work', resetModel=True))
    assert 'nextMessageFailure' not in app._session()
    assert app._session()['pendingBundleSelection']['resetModel'] is True


async def test_revoked_original_bundle_grant_is_not_renewed_by_later_send(app, monkeypatch):
    from amplifier_web import draft_defaults
    from amplifier_web.next_message_configuration import save, prepare
    from amplifier_web.runtime import RuntimeStartupError
    session = app._session()
    grant = (await app.dispatch('capability.grant', {'sessionId': session['id'],
        'effects': ['bundles'], 'scopes': ['session'], 'reason': 'Synthetic deferred bundle'}))['result']
    authority = {'grantId': grant['id'], 'grantRevision': grant['revision'],
        'sessionId': session['id'], 'workspace': session['workspace'], 'interruptionRevision': 0,
        'effect': 'bundles', 'scope': 'session'}
    # Exercise the same retained authority payload used by delayed host actions.
    save(app, choices(app, bundle='work'), authority)
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'bundle': 'work', 'modelCompatible': True}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    attempt = asyncio.create_task(prepare(app, deepcopy(session)))
    await asyncio.wait_for(entered.wait(), 2)
    await app.dispatch('capability.revoke', {'sessionId': session['id'], 'id': grant['id'],
        'expectedRevision': grant['revision']})
    release.set()
    with pytest.raises(RuntimeStartupError):
        await attempt
    assert app.runtime is None
    assert session['pendingBundleSelection']['authority'] == authority
    assert session['pendingBundleSelection']['bundle'] == 'work'
    assert session['nextMessageStatus'] == 'not-applied'


@pytest.mark.parametrize('stop', [False, True])
async def test_worker_stop_after_host_allow_keeps_rollback_journal(tmp_path, stop):
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.bundle_selection import BundleTransaction
    worker = Worker()
    transaction = BundleTransaction(tmp_path/'app', tmp_path, 'synthetic')
    transaction.begin()
    before = transaction.path.read_bytes()
    async def allow(*args):
        if stop:
            worker.stop_revision += 1
        return {'allowed': True}
    worker.bridge = allow
    if stop:
        with pytest.raises(RuntimeError, match='stopped before commit'):
            await worker.commit_replacement(transaction, deferred=True, stop_revision=0)
        assert transaction.path.read_bytes() == before
        assert transaction.restore()
    else:
        await worker.commit_replacement(transaction, deferred=True, stop_revision=0)
        assert not transaction.path.exists()
