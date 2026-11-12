"""Whole-profile selection: admission, storage and observed rendering are distinct."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web.service import AppError, AppService, ACTION_DEFINITIONS
from amplifier_web.shell_modules import DEFAULT
from amplifier_web.appearance_preferences import DEFAULTS
from amplifier_web import themes


@pytest.fixture
async def app(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    queues = []
    for identity in ('one', 'two'):
        service.clients.attach(identity)
        with service.clients.bind(identity):
            queues.append(service.subscribe())
        await report(service, client=identity)
    yield service
    for queue in queues:
        service.unsubscribe(queue)
    await service.close()


async def call(app, action, args=None, client='one', *, expected_accepted=True, **kwargs):
    with app.clients.bind(client):
        receipt = await app.dispatch(action, args or {}, include_state=False, **kwargs)
        assert receipt['accepted'] is expected_accepted
        return receipt.get('result')


def local_cas(app, client='one'):
    current = app.appearance.read(client)
    return {'generation': current['generation'], 'expectedRevision': current['local']['revision'],
            'expectedStorageVersion': current['local']['storageVersion']}


async def update(app, patch, *, scope='shared', client='one', **kwargs):
    current = app.appearance.read(client)
    args = {'scope': scope, 'patch': patch, **(local_cas(app, client) if scope == 'client' else {
        'expectedRevision': current['shared']['revision']})}
    return await call(app, 'appearance.preferences.update', args, client, **kwargs)


async def select(app, scope='client', *, client='one', **extra):
    current = app.appearance.read(client)
    return await call(app, 'appearance.preferences.select', {
        **local_cas(app, client), 'activeScope': scope,
        **({'expectedSharedRevision': current['shared']['revision']} if scope == 'client'
           and current['local']['values'] is None else {}), **extra}, client)


async def report(app, *, client='one', **extra):
    current = app.appearance.read(client)
    return await call(app, 'appearance.client.report', {
        'generation': current['generation'], 'revision': current['local']['revision'],
        'storageVersion': current['local']['storageVersion'], 'activeScope': current['activeScope'],
        'values': current['local']['values'], 'status': 'ready', **extra}, client, origin='ui')


async def ack(app, *, client='one', status='stored', **extra):
    current = app.appearance.read(client)
    command = current['command']
    return await call(app, 'appearance.client.report', {
        'generation': current['generation'], 'revision': command['expectedRevision'] + 1,
        'storageVersion': uuid.uuid4().hex, **command['after'],
        'commandId': command['id'], 'status': status, **extra}, client, origin='ui')


async def initialize(app, *, client='one'):
    await select(app, client=client)
    return await ack(app, client=client)


async def test_first_copy_is_complete_independent_and_not_activated_before_ack(app):
    await update(app, {'appearanceId': 'reference', 'scheme': 'light',
                       'executionVisibility': {'step': False}, 'executionMetrics': [], 'decorations': False})
    before = app.appearance.shared()
    queued = await select(app)
    assert queued['status'] == 'awaiting-storage' and not queued['stored'] and not queued['activated']
    pending = app.appearance.read('one')
    assert pending['activeScope'] == 'shared' and pending['local']['values'] is None
    assert pending['command']['after'] == {'activeScope': 'client', 'values': before['values']}
    assert not ({'patch', 'clear'} & pending['command'].keys())
    await update(app, {'scheme': 'dark', 'executionVisibility': {'turn': False}, 'decorations': True})
    stored = await ack(app)
    assert stored['stored'] and stored['activated'] and not stored['rendered']
    local = app.appearance.read('one')
    assert local['effective'] == before['values'] == local['local']['values']
    assert set(DEFAULTS) <= local['effective'].keys() and 'accent' not in local['effective']
    assert local['effective']['executionMetrics'] == [] and local['effective']['decorations'] is False
    assert local['source'] == {'scope': 'client', 'revision': 1, 'storageVersion': local['local']['storageVersion']}
    assert local['writeScope'] == local['activeScope'] == 'client'
    assert not ({'sources', 'writeScopes', 'overrides'} & local.keys())
    composition = local['composition']['id']
    await update(app, {'conversationStatus': True, 'accent': '#123456'})
    assert app.appearance.read('one')['composition']['id'] == composition
    assert app.appearance.values('one') == before['values']
    assert app.appearance.read('two')['effective']['conversationStatus'] is True
    assert app.appearance.shared()['revision'] == before['revision'] + 2


async def test_nested_visibility_defaults_and_merge_are_in_profile_only(app):
    await update(app, {'executionVisibility': {'turn': False, 'step': False}})
    await initialize(app)
    await update(app, {'executionVisibility': {'conversation': False}}, scope='client')
    await ack(app)
    await update(app, {'executionVisibility': {'step': True, 'turn': True}})
    assert app.appearance.values('one')['executionVisibility'] == {'step': False, 'turn': False, 'conversation': False}
    # An empty nested map is complete/intrinsic, not a request to inherit Global.
    values = {**deepcopy(DEFAULTS), 'executionVisibility': {}, 'executionMetrics': [], 'decorations': False}
    await report(app, revision=app.appearance.read('one')['local']['revision'] + 1,
                 activeScope='client', storageVersion='intrinsic', values=values)
    assert app.appearance.values('one') == values
    await update(app, {'executionVisibility': {'step': False}}, scope='client')
    await ack(app)
    assert app.appearance.values('one')['executionVisibility'] == {'step': False}
    assert app.appearance.values('one')['executionMetrics'] == []
    assert app.appearance.values('one')['decorations'] is False


@pytest.mark.parametrize('scope', ['shared', 'client'])
async def test_appearance_switch_clears_accent_and_old_undo_cannot_replace_new_profile(app, scope):
    if scope == 'client':
        await initialize(app)
    async def edit(patch):
        result = await update(app, patch, scope=scope)
        return await ack(app) if scope == 'client' else result
    colored = await edit({'accent': '#dd44aa', 'density': 'compact', 'decorations': False})
    await edit({'appearanceId': 'builtin:digital-rain'})
    current = app.appearance.read('one')
    assert current['effective']['appearanceId'] == 'builtin:digital-rain'
    assert 'accent' not in current['effective']
    assert current['effective']['density'] == 'compact'
    assert current['effective']['decorations'] is False
    before_rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    with pytest.raises(AppError) as error:
        await call(app, 'appearance.preferences.undo', {'undoId': colored['undoId']})
    assert error.value.status == 409
    assert error.value.code == 'appearance_undo_stale'
    assert app.appearance.read('one') == current
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == before_rows


@pytest.mark.parametrize('scope', ['shared', 'client'])
async def test_accent_reset_preserves_current_look_other_preferences_and_normal_undo(app, scope):
    if scope == 'client':
        await initialize(app)
    async def edit(patch):
        result = await update(app, patch, scope=scope)
        return await ack(app) if scope == 'client' else result
    await edit({'appearanceId': 'builtin:digital-rain', 'accent': '#dd44aa', 'scheme': 'dark',
                'executionVisibility': {'turn': False}, 'decorations': False})
    colored = deepcopy(app.appearance.read('one')['effective'])
    await edit({'appearanceId': 'builtin:digital-rain'})
    assert app.appearance.read('one')['effective'] == colored
    reset = await update(app, {'accent': None}, scope=scope)
    if scope == 'client':
        pending = app.appearance.read('one')
        assert pending['effective'] == colored
        assert 'accent' not in pending['command']['after']['values']
        reset = await ack(app)
    expected = {key: value for key, value in colored.items() if key != 'accent'}
    assert app.appearance.read('one')['effective'] == expected
    await call(app, 'appearance.preferences.undo', {'undoId': reset['undoId']})
    if scope == 'client':
        await ack(app)
    assert app.appearance.read('one')['effective'] == colored
    await edit({'appearanceId': 'builtin:graphite', 'accent': '#123456'})
    assert app.appearance.read('one')['effective']['accent'] == '#123456'


async def test_browser_accent_reset_failure_and_stale_cas_never_change_shared_or_local(app):
    await initialize(app)
    await update(app, {'accent': '#dd44aa'}, scope='client')
    await ack(app)
    local = deepcopy(app.appearance.read('one')['effective'])
    shared = app.appearance.shared()
    stale = local_cas(app)
    await update(app, {'accent': None}, scope='client')
    failed = await ack(app, status='failed', error='storage denied')
    assert failed['status'] == 'failed'
    assert app.appearance.read('one')['effective'] == local
    assert app.appearance.shared() == shared
    await update(app, {'accent': None}, scope='client')
    await ack(app)
    current = app.appearance.read('one')
    assert 'accent' not in current['effective']
    with pytest.raises(AppError):
        await call(app, 'appearance.preferences.update', {
            **stale, 'scope': 'client', 'patch': {'accent': '#123456'}})
    assert app.appearance.read('one') == current
    assert app.appearance.shared() == shared
    with pytest.raises(AppError):
        await report(app, revision=current['local']['revision'] + 1, storageVersion='invalid-null-accent',
                     activeScope='client', values={**current['effective'], 'accent': None})
    assert app.appearance.read('one') == current


@pytest.mark.parametrize('scope', ['shared', 'client'])
async def test_current_package_switch_undo_restores_old_appearance_and_accent(app, scope):
    if scope == 'client':
        await initialize(app)
    await update(app, {'appearanceId': 'builtin:aurora', 'accent': '#dd44aa'}, scope=scope)
    if scope == 'client':
        await ack(app)
    before = deepcopy(app.appearance.read('one')['effective'])
    switched = await update(app, {'appearanceId': 'builtin:digital-rain'}, scope=scope)
    if scope == 'client':
        switched = await ack(app)
    assert 'accent' not in app.appearance.read('one')['effective']
    await call(app, 'appearance.preferences.undo', {'undoId': switched['undoId']})
    if scope == 'client':
        await ack(app)
    assert app.appearance.read('one')['effective'] == before


async def test_complete_browser_report_reload_and_scope_switch_preserve_deliberate_accent(app):
    await update(app, {'appearanceId': 'builtin:graphite', 'accent': '#123456'})
    global_before = app.appearance.shared()
    values = {**deepcopy(DEFAULTS), 'appearanceId': 'builtin:lightline', 'accent': '#dd44aa'}
    await report(app, revision=1, activeScope='client', values=values, storageVersion='retained-colored-record')
    assert app.appearance.read('one')['effective'] == values
    await select(app, 'shared')
    await ack(app)
    assert app.appearance.read('one')['effective'] == global_before['values']
    await select(app, 'client')
    await ack(app)
    assert app.appearance.read('one')['effective'] == values
    app.clients.attach('reload', resume='one')
    await report(app, client='reload', revision=0, activeScope='client', values=values,
                 storageVersion=app.appearance.read('one')['local']['storageVersion'])
    assert app.appearance.read('reload')['effective'] == values
    assert app.appearance.shared() == global_before


async def test_shell_mixed_client_edit_retains_accent_and_undo_whole_record(app):
    await initialize(app)
    await update(app, {'appearanceId': 'builtin:digital-rain', 'accent': '#dd44aa'}, scope='client')
    await ack(app)
    before = deepcopy(app.appearance.read('one')['effective'])
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    await apply(app, prepared)
    assert app.appearance.read('one')['effective'] == before
    stored = await ack(app)
    assert app.appearance.read('one')['effective'] == {**before, 'scheme': 'dark'}
    await call(app, 'appearance.preferences.undo', {'undoId': stored['undoId']})
    await ack(app)
    assert app.appearance.read('one')['effective'] == before


async def test_switches_retain_both_profiles_and_never_publish(app):
    await update(app, {'scheme': 'light'})
    await initialize(app)
    await update(app, {'scheme': 'dark', 'decorations': False}, scope='client')
    await ack(app)
    local = deepcopy(app.appearance.read('one')['local']['values'])
    global_before = app.appearance.shared()
    await select(app, 'shared')
    assert app.appearance.read('one')['activeScope'] == 'client'
    await ack(app)
    read = app.appearance.read('one')
    assert read['activeScope'] == 'shared' and read['effective']['scheme'] == 'light'
    assert read['local']['values'] == local
    await update(app, {'scheme': 'system'})
    await select(app)
    await ack(app)
    assert app.appearance.read('one')['effective'] == local
    assert app.appearance.shared()['revision'] == global_before['revision'] + 1


async def test_first_copy_requires_current_shared_cas_before_admission(app):
    args = {**local_cas(app), 'activeScope': 'client', 'expectedSharedRevision': 0}
    await update(app, {'decorations': False})
    for value in (args, {key: value for key, value in args.items() if key != 'expectedSharedRevision'}):
        with pytest.raises(AppError, match='Shared preferences changed'):
            await call(app, 'appearance.preferences.select', value)
    assert 'command' not in app.appearance.read('one')
    await select(app)
    seeded = deepcopy(app.appearance.read('one')['command']['after'])
    await update(app, {'decorations': True})
    await ack(app)
    assert app.appearance.read('one')['local']['values'] == seeded['values']


async def test_client_update_requires_initialized_profile_and_never_implicitly_selects(app):
    with pytest.raises(AppError, match='initialize'):
        await update(app, {'scheme': 'dark'}, scope='client')
    await initialize(app)
    await select(app, 'shared')
    await ack(app)
    shared = app.appearance.shared()
    await update(app, {'scheme': 'light'}, scope='client')
    command = app.appearance.read('one')['command']
    assert command['after']['activeScope'] == 'shared'
    stored = await ack(app)
    assert stored['stored'] and not stored['activated']
    assert app.appearance.read('one')['effective'] == shared['values']
    assert app.appearance.read('one')['local']['values']['scheme'] == 'light'
    assert app.appearance.shared() == shared


async def test_updates_capture_destination_not_later_active_source(app):
    await initialize(app)
    captured_shared_revision = app.appearance.shared()['revision']
    await select(app, 'shared')
    await ack(app)
    captured = local_cas(app)
    await select(app, 'client')
    await ack(app)
    # A captured Global edit still edits Global, never the now-active browser.
    await call(app, 'appearance.preferences.update', {'scope': 'shared',
        'expectedRevision': captured_shared_revision, 'patch': {'scheme': 'dark'}})
    assert app.appearance.values('one')['scheme'] == 'system'
    with pytest.raises(AppError, match='Browser preferences changed'):
        await call(app, 'appearance.preferences.update', {**captured, 'scope': 'client', 'patch': {'scheme': 'light'}})


@pytest.mark.parametrize('field,value', [('generation', 'obsolete'), ('expectedRevision', 99),
                                         ('expectedStorageVersion', 'obsolete')])
@pytest.mark.parametrize('operation', ['select', 'update'])
async def test_local_mutations_check_all_cas_dimensions(app, field, value, operation):
    await initialize(app)
    args = {**local_cas(app), **({'activeScope': 'shared'} if operation == 'select'
        else {'scope': 'client', 'patch': {'scheme': 'dark'}}), field: value}
    before = app.appearance.read('one')
    with pytest.raises(AppError) as error:
        await call(app, 'appearance.preferences.' + operation, args)
    assert error.value.status == 409 and app.appearance.read('one') == before


@pytest.mark.parametrize('change', ['scope', 'values', 'revision', 'version', 'generation', 'command'])
async def test_ack_matches_whole_after_and_fresh_version_exactly(app, change):
    await select(app)
    current = app.appearance.read('one')
    extra = {'scope': {'activeScope': 'shared'},
             'values': {'values': {**current['command']['after']['values'], 'scheme': 'dark'}},
             'revision': {'revision': 0}, 'version': {'storageVersion': 'missing'},
             'generation': {'generation': 'obsolete'}, 'command': {'commandId': 'wrong'}}[change]
    with pytest.raises(AppError) as error:
        await ack(app, **extra)
    assert error.value.status == 409
    assert app.appearance.read('one') == current
    assert app.appearance.read('one')['activeScope'] == 'shared'


async def test_storage_failure_retains_last_confirmed_source_and_no_replay(app):
    await initialize(app)
    await update(app, {'scheme': 'dark'}, scope='client')
    await ack(app)
    confirmed = app.appearance.read('one')
    shared = app.appearance.shared()
    queued = await select(app, 'shared')
    failed = await ack(app, status='failed', error='Quota exceeded')
    assert failed['status'] == 'failed' and not failed['stored'] and not failed['activated']
    current = app.appearance.read('one')
    assert current['activeScope'] == 'client' and current['effective'] == confirmed['effective']
    assert current['local']['error'] == 'Quota exceeded' and app.appearance.shared() == shared
    # An HTTP retry of admitted work cannot manufacture a second command.
    assert current['command']['id'] == queued['commandId'] and current['command']['status'] == 'failed'
    await report(app, status='failed', error='Storage unavailable')
    assert app.appearance.read('one')['effective'] == confirmed['effective']


async def test_pending_unknown_storage_is_not_replayed_and_can_be_reconciled(app):
    await select(app)
    command = deepcopy(app.appearance.read('one')['command'])
    with pytest.raises(AppError, match='awaiting storage'):
        await select(app)
    assert app.appearance.read('one')['command'] == command
    await report(app, revision=1, storageVersion='persisted-without-ack', **command['after'])
    read = app.appearance.read('one')
    assert read['activeScope'] == 'client' and read['command']['status'] == 'superseded'
    with pytest.raises(AppError, match='match'):
        await ack(app, commandId='different-command')
    assert app.shell.client('one')['revision'] == 0


async def test_source_admission_retry_is_bound_to_exact_document_and_arguments(app):
    args = {**local_cas(app), 'activeScope': 'client',
            'expectedSharedRevision': app.appearance.shared()['revision']}
    first = await call(app, 'appearance.preferences.select', args, command_id='select-once')
    command = deepcopy(app.appearance.read('one')['command'])
    assert await call(app, 'appearance.preferences.select', args, command_id='select-once') == first
    assert app.appearance.read('one')['command'] == command
    with pytest.raises(AppError, match='different arguments'):
        await call(app, 'appearance.preferences.select', {**args, 'activeScope': 'shared'}, command_id='select-once')
    with pytest.raises(AppError, match='different arguments'):
        await call(app, 'appearance.preferences.select', args, client='two', command_id='select-once')
    await ack(app)
    # A settled admission receipt is not another storage command after a lost response.
    assert await call(app, 'appearance.preferences.select', args, command_id='select-once') == first
    assert app.appearance.read('one')['command']['status'] == 'stored'


async def test_reports_origin_generation_revision_and_aba_fences(app):
    await initialize(app)
    old = app.appearance.read('one')
    await update(app, {'scheme': 'dark'}, scope='client')
    await ack(app)
    with pytest.raises(AppError, match='newer storage revision'):
        await report(app, values=old['local']['values'], storageVersion='changed-with-same-revision')
    with pytest.raises(AppError, match='fresh storage version'):
        await report(app, revision=3, values=old['local']['values'])
    with pytest.raises(AppError, match='cannot be reused'):
        await report(app, revision=3, values=old['local']['values'], storageVersion=old['local']['storageVersion'])
    with pytest.raises(AppError, match='generation'):
        await report(app, generation='obsolete')
    with pytest.raises(AppError, match='originating browser'):
        await call(app, 'appearance.client.report', {'generation': old['generation'], 'revision': 1,
            'storageVersion': old['local']['storageVersion'], 'activeScope': 'client',
            'values': old['local']['values'], 'status': 'ready'}, origin='agent')
    with pytest.raises(AppError, match='different client'):
        await call(app, 'appearance.preferences.read', {'clientId': 'two'})
    await report(app, revision=3, activeScope='shared', storageVersion='missing', values=None)
    assert app.appearance.read('one')['activeScope'] == 'shared'
    await select(app)
    with pytest.raises(AppError, match='match'):
        await ack(app, storageVersion=old['local']['storageVersion'])


@pytest.mark.parametrize('values,scope,version', [({}, 'client', 'one'),
    ({'scheme': 'dark'}, 'shared', 'two'), (None, 'client', 'three'),
    (deepcopy(DEFAULTS), 'client', 'missing')])
async def test_invalid_incomplete_browser_records_never_become_fallbacks(app, values, scope, version):
    before = app.appearance.read('one')
    with pytest.raises(AppError):
        await report(app, values=values, activeScope=scope, storageVersion=version)
    assert app.appearance.read('one') == before


async def test_reload_and_new_tab_report_record_without_copying_preview_or_editor(app):
    await initialize(app)
    await update(app, {'scheme': 'dark'}, scope='client')
    await ack(app)
    await call(app, 'appearance.preview', {'name': 'Draft', 'css': '#amp-one{color:blue}'})
    app.clients.records['one']['view']['themeEditor'] = {'css': 'private editor body'}
    confirmed = app.appearance.read('one')
    app.clients.attach('reload', resume='one')
    reloaded = app.appearance.read('reload')
    assert reloaded['generation'] != confirmed['generation'] and reloaded['preview'] is None
    assert reloaded['activeScope'] == 'shared' and reloaded['local']['values'] is None
    for target in ('reload', 'two'):
        local = app.appearance.read(target)['local']
        await report(app, client=target, activeScope='client', values=confirmed['local']['values'],
                     revision=local['revision'] + int(local['reported']),
                     storageVersion=confirmed['local']['storageVersion'])
        assert app.appearance.read(target)['effective'] == confirmed['effective']
        assert app.appearance.read(target)['preview'] is None
    assert app.appearance.read('one')['preview'] == confirmed['preview']
    assert 'private editor body' not in str(await call(app, 'appearance.preferences.read', client='two'))
    await report(app, client='reload', revision=1, activeScope='shared', values=None, storageVersion='missing')
    assert app.appearance.values('reload') == app.appearance.shared()['values']
    assert app.appearance.values('one')['scheme'] == 'dark'


async def test_cross_tab_source_report_retains_other_tab_preview_and_editor(app):
    await initialize(app)
    await select(app, 'shared')
    await ack(app)
    current = app.appearance.read('one')
    app.clients.records['two']['view']['themeEditor'] = {'css': 'private body'}
    await call(app, 'appearance.preview', {'name': 'Two', 'css': '#amp-one{color:blue}'}, client='two')
    preview = app.appearance.read('two')['preview']
    await report(app, client='two', revision=1, values=current['local']['values'],
                 storageVersion=current['local']['storageVersion'], activeScope='shared')
    assert app.appearance.read('two')['local']['values'] == current['local']['values']
    assert app.appearance.read('two')['preview'] == preview
    assert app.clients.records['two']['view']['themeEditor'] == {'css': 'private body'}


async def test_disconnect_retains_confirmed_values_without_issuing_command(app):
    await initialize(app)
    for queue in list(app.queues):
        if app.queue_clients.get(queue) == 'one':
            app.unsubscribe(queue)
    before = app.appearance.read('one')
    assert before['availability'] == 'disconnected'
    with pytest.raises(AppError, match='disconnected'):
        await select(app, 'shared')
    assert app.appearance.read('one') == before


@pytest.mark.parametrize('operation', ['first-create', 'edit', 'source', 'inactive'])
async def test_browser_undo_restores_whole_record_with_fresh_version_even_after_global_edit(app, operation):
    if operation != 'first-create':
        await initialize(app)
    if operation == 'inactive':
        await select(app, 'shared')
        await ack(app)
    before = app.appearance.read('one')
    if operation in {'edit', 'inactive'}:
        await update(app, {'scheme': 'dark', 'executionVisibility': {'step': False}}, scope='client')
    else:
        await select(app, 'shared' if operation == 'source' else 'client')
    stored = await ack(app)
    edited = app.appearance.read('one')
    await update(app, {'scheme': 'light', 'decorations': False})
    await call(app, 'appearance.preferences.undo', {'undoId': stored['undoId']})
    assert app.appearance.read('one')['local']['values'] == edited['local']['values']
    await ack(app)
    restored = app.appearance.read('one')
    assert restored['activeScope'] == before['activeScope']
    assert restored['local']['values'] == before['local']['values']
    assert restored['local']['storageVersion'] not in {before['local']['storageVersion'], edited['local']['storageVersion']}
    if operation == 'first-create':
        assert restored['activeScope'] == 'shared' and restored['local']['values'] is None
        assert restored['effective']['scheme'] == 'light'
    with pytest.raises(AppError, match='Newer browser'):
        await call(app, 'appearance.preferences.undo', {'undoId': stored['undoId']})


async def test_shared_undo_and_stale_edits_keep_cas_and_private_drafts(app):
    app.clients.records['one']['view']['themeEditor'] = {'css': 'private body'}
    saved = await update(app, {'scheme': 'dark'})
    await call(app, 'appearance.preferences.undo', {'undoId': saved['undoId']})
    assert app.appearance.shared()['values'] == DEFAULTS
    saved = await update(app, {'scheme': 'light'})
    await update(app, {'decorations': False}, client=None)
    with pytest.raises(AppError, match='Newer shared'):
        await call(app, 'appearance.preferences.undo', {'undoId': saved['undoId']})
    with pytest.raises(AppError, match='Shared preferences changed'):
        await call(app, 'appearance.preferences.update', {'scope': 'shared', 'expectedRevision': 0, 'patch': {'scheme': 'dark'}})
    assert 'private body' not in str(app.appearance.read('one'))


async def test_preview_cancel_is_not_undo_and_render_observation_matches_exact_identity(app):
    await initialize(app)
    saved = await update(app, {'scheme': 'dark'}, scope='client')
    stored = await ack(app)
    assert not saved['activated']
    await call(app, 'appearance.preview', {'name': 'Exact', 'css': '#amp-one{--a-accent:#123456}'})
    current = app.appearance.read('one')
    observed = await report(app, renderedCompositionId=current['composition']['id'])
    assert observed['rendered']
    await call(app, 'appearance.preview.cancel')
    assert app.appearance.values('one')['scheme'] == 'dark'
    await call(app, 'appearance.preferences.undo', {'undoId': stored['undoId']})
    await ack(app)
    assert app.appearance.values('one')['scheme'] == 'system' and app.appearance.read('one')['preview'] is None
    stale = await report(app, renderedCompositionId=current['composition']['id'])
    assert not stale.get('rendered')


async def test_shell_preview_readbacks_overlay_only_selected_complete_profile(app):
    await initialize(app)
    await update(app, {'decorations': False, 'executionVisibility': {'turn': False}}, scope='client')
    await ack(app)
    await update(app, {'decorations': True, 'executionVisibility': {'step': False}})
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    await call(app, 'shell.changes.preview', {'clientId': 'one', 'expectedRevision': 0, 'changeId': prepared['id']})
    expected = {**app.appearance.values('one'), 'scheme': 'dark'}
    actual = app.shell.inspect('one')['effectiveComposition']['presentation']
    assert actual == {key: value for key, value in expected.items() if key != 'appearanceId'}
    assert app.appearance.read('one')['renderedPreferences'] == expected
    assert app.appearance.values('one')['scheme'] == 'system'


async def test_stable_owner_namespace_and_global_persist_not_browser_mirror(app, tmp_path):
    first = app.appearance.read('one')['namespace']
    assert first == app.appearance.read('two')['namespace']
    await update(app, {'scheme': 'dark'}, client=None)
    await initialize(app)
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    other = AppService(tmp_path / 'other', workspace=tmp_path)
    try:
        assert restored.appearance.read()['namespace'] == first
        assert restored.appearance.values()['scheme'] == 'dark'
        assert other.appearance.read()['namespace'] != first
        assert restored.appearance.read('one')['local']['reported'] is False
        assert restored.appearance.read('one')['local']['values'] is None
    finally:
        await restored.close()
        await other.close()


async def test_normal_startup_is_enabled_without_global_write(tmp_path, monkeypatch):
    monkeypatch.delenv('AMPLIFIER_APPEARANCE_EXPERIMENT', raising=False)
    service = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        assert service.appearance.read()['effective'] == DEFAULTS
        assert service.appearance.read()['namespace'].startswith('amplifier.appearance.v2:')
        assert service.shell.get('preferences', 'shared-reference-v2') is None
        assert service.shell.get('appearance-identity', 'v2')['owner'] == service.appearance.owner
        await service.dispatch('appearance.preferences.read')
        assert service.shell.get('preferences', 'shared-reference-v2') is None
        assert not service.appearance.mirrors
    finally:
        await service.close()


async def test_local_only_cold_restart_keeps_namespace_before_first_global_write(app, tmp_path):
    first = app.appearance.read('one')
    await initialize(app)
    await update(app, {'scheme': 'dark', 'appearanceId': 'builtin:graphite'}, scope='client')
    await ack(app)
    local = app.appearance.read('one')
    assert app.shell.get('preferences', 'shared-reference-v2') is None
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert restored.appearance.read('one')['namespace'] == first['namespace']
        assert restored.appearance.read('one')['generation'] != first['generation']
        await report(restored, activeScope='client', values=local['local']['values'],
                     storageVersion=local['local']['storageVersion'])
        assert restored.appearance.read('one')['effective'] == local['effective']
        await update(restored, {'scheme': 'light'}, client=None)
        assert restored.appearance.read('one')['namespace'] == first['namespace']
        assert restored.appearance.read('one')['effective'] == local['effective']
        assert restored.shell.get('preferences', 'shared-reference-v2')['owner'] == restored.appearance.owner
    finally:
        await restored.close()


@pytest.mark.parametrize('malformed', [
    None,
    {'owner': 'bad', 'revision': 0, 'values': DEFAULTS},
    {'owner': 'a' * 32, 'revision': True, 'values': DEFAULTS},
    {'owner': 'a' * 32, 'revision': 1, 'values': {'appearanceId': 'builtin:default'}},
    {'owner': 'a' * 32, 'revision': 1, 'values': {**DEFAULTS, 'accent': None}},
    {'owner': 'a' * 32, 'revision': 1, 'values': None},
])
async def test_malformed_new_profile_explicit_failure_never_rewrites_or_seeds(tmp_path, malformed):
    import json
    data = tmp_path / 'data'
    first = AppService(data, workspace=tmp_path)
    first.shell.put('preferences', 'shared-reference-v2', malformed)
    before = list(first.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    await first.close()
    restored = AppService(data, workspace=tmp_path)
    try:
        failed = restored.appearance.read()
        assert failed['availability'] == 'failed'
        assert failed['code'] == 'appearance_preferences_invalid'
        assert not {'namespace', 'theme', 'effective', 'composition'} & failed.keys()
        assert list(restored.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == before
        for action, args in [
            ('appearance.preferences.update', {'scope': 'shared', 'expectedRevision': 1, 'patch': {'scheme': 'dark'}}),
            ('appearance.preview', {'name': 'Preview', 'css': '#amp-one{color:red}'})]:
            with pytest.raises(AppError) as error:
                await restored.dispatch(action, args)
            assert error.value.code == 'appearance_preferences_invalid'
        assert restored.shell.get('preferences', 'shared-reference-v2') == malformed
        assert list(restored.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == before
    finally:
        await restored.close()


@pytest.mark.parametrize('kind,identity', [
    ('preferences', 'shared-reference-v2'), ('appearance-identity', 'v2')])
async def test_literal_null_new_record_after_startup_fails_without_overwrite(app, kind, identity):
    app.shell.put(kind, identity, None)
    before = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    failed = app.appearance.read('one')
    assert failed['availability'] == 'failed'
    assert failed['code'] == 'appearance_preferences_invalid'
    with pytest.raises(AppError) as error:
        await update(app, {'scheme': 'dark'})
    assert error.value.code == 'appearance_preferences_invalid'
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == before


async def test_literal_null_new_identity_cold_start_fails_without_new_owner(tmp_path):
    data = tmp_path / 'data'
    first = AppService(data, workspace=tmp_path)
    first.shell.put('appearance-identity', 'v2', None)
    await first.close()
    restored = AppService(data, workspace=tmp_path)
    try:
        assert restored.appearance.read()['availability'] == 'failed'
        assert restored.appearance.namespace is None
        assert restored.shell.get('appearance-identity', 'v2') is None
        assert restored.db.execute("SELECT value FROM shell_records WHERE kind='appearance-identity' AND id='v2'").fetchone() == ('null',)
        assert restored.shell.get('preferences', 'shared-reference-v2') is None
    finally:
        await restored.close()


async def test_upgrade_reload_preserves_custom_component_views_but_requires_revalidation(app):
    import hashlib
    import json
    from amplifier_web.shell_modules import encoded
    manifest = {'id': 'custom.retained', 'version': '1.0.0', 'apiVersion': '1.0',
                'profile': 'trusted-native-component-v1', 'label': 'Retained actions',
                'slots': ['app.actions'], 'stateSchema': 'retained-v1', 'capabilities': ['shell.read']}
    source = 'export function mount(){return {dispose(){}}}'
    digest = hashlib.sha256((encoded(manifest) + '\n' + source).encode()).hexdigest()
    (app.shell.directory / (digest + '.mjs')).write_text(source)
    app.shell.put('package', digest, {'manifest': manifest, 'validation': {
        'status': 'passed', 'hostFingerprint': 'old-accepted-host'}})
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'retained-action', 'package': digest, 'slot': 'app.actions'})
    composition['presentation'] = {'scheme': 'dark', 'accent': '#e94a9f'}
    views = {'retained-action': {'view': {'expanded': True}, 'stateSchema': 'retained-v1', 'generation': 4},
             'chats': {'view': {'navFilter': 'keep filter'}, 'generation': 2}}
    legacy = {'revision': 4, 'composition': composition, 'lastGood': composition,
              'baselineView': {'navFilter': 'keep baseline'}, 'views': views,
              'preview': {'id': 'old-preview', 'composition': composition},
              'preferenceCommand': {'id': 'old-pending'}, 'reported': {'status': 'ready'}}
    app.db.execute('INSERT INTO shell_records VALUES (?,?,?)', ('client', 'old-document', json.dumps(legacy)))
    app.clients.attach('old-document')
    app.clients.records['old-document']['canvas'] = {'id': 'retained-canvas', 'open': False, 'view': {'zoom': 2}}
    app.db.commit()
    # New document identity is the actual browser resume path, not same-ID read.
    app.clients.attach('upgraded-document', resume='old-document')
    client = app.shell.client('upgraded-document')
    assert client['composition']['presentation'] == {}
    assert client['composition']['instances'] == composition['instances']
    assert client['views'] == views
    assert client['baselineView'] == {'navFilter': 'keep baseline'}
    assert client['preview'] is None and client['reported'] is None
    assert 'preferenceCommand' not in client
    assert app.clients.records['upgraded-document']['canvas']['id'] == 'retained-canvas'
    assert app.db.execute("SELECT value FROM shell_records WHERE kind='client' AND id='old-document'").fetchone()[0] == json.dumps(legacy)
    inspected = app.shell.inspect('upgraded-document')
    assert 'requires current host validation' in inspected['packages'][digest]['error']
    assert app.shell.inspect('upgraded-document', recovery=True)['effectiveComposition'] == DEFAULT
    with pytest.raises(AppError, match='requires current host validation'):
        app.shell.check_composition(client['composition'])
    # Validation admission is still required; no old receipt is promoted.
    assert app.shell.get('package', digest)['validation']['hostFingerprint'] == 'old-accepted-host'


async def test_missing_new_selected_package_is_visible_reference_recovery_without_preference_write(app):
    await update(app, {'appearanceId': 'builtin:graphite'})
    shared = app.appearance.shared()
    shared['values']['appearanceId'] = 'saved:missing.zip'
    app.shell.put('preferences', 'shared-reference-v2', shared)
    before = app.shell.get('preferences', 'shared-reference-v2')
    current = app.appearance.read('one')
    assert current['theme']['available'] is False
    assert current['theme']['recoveryId'] == 'reference'
    assert current['effective']['appearanceId'] == 'saved:missing.zip'
    assert app.shell.get('preferences', 'shared-reference-v2') == before


@pytest.mark.parametrize('legacy_present', [True, False])
async def test_production_restart_preserves_legacy_theme_profiles_and_library(tmp_path, monkeypatch, legacy_present):
    import json
    from amplifier_web import appearance_packages as packages
    monkeypatch.delenv('AMPLIFIER_APPEARANCE_EXPERIMENT', raising=False)
    path = tmp_path / 'data'
    service = AppService(path, workspace=tmp_path)
    retained = {'name': 'Retained complete skin', 'css': '#amp-one{color:#123456}'}
    if legacy_present:
        service._state['theme'] = deepcopy(retained)
    else:
        service._state.pop('theme', None)
    profile = {'owner': 'old-owner', 'revision': 89, 'values': {'appearanceId': 'reference', 'scheme': 'dark'}}
    for key in ('appearance', 'reference-v1', 'shared-reference-v1', 'appearance-experiment-0801-v1'):
        service.shell.put('preferences', key, profile)
    old_client = {'revision': 9, 'composition': deepcopy(DEFAULT), 'lastGood': deepcopy(DEFAULT),
                  'views': {}, 'preview': None, 'preferenceCommand': {'id': 'old-uncertain'}}
    old_client['composition']['presentation'] = {'scheme': 'dark', 'decorations': False}
    old_client['lastGood']['presentation'] = {'scheme': 'dark'}
    service.db.execute('INSERT INTO shell_records VALUES (?,?,?)', ('client', 'retained-document', json.dumps(old_client)))
    root = path / 'themes'
    root.mkdir()
    legacy_css = b'#amp-one{color:#654321}'
    newer_zip = packages.encode(packages.from_css('#amp-one{--a-accent:#456789}', 'Retained delta').files)
    (root / 'retained.css').write_bytes(legacy_css)
    (root / 'retained.zip').write_bytes(newer_zip)
    service._save()
    before = list(service.db.execute("SELECT kind,id,value FROM shell_records WHERE id IN ('appearance','reference-v1','shared-reference-v1','appearance-experiment-0801-v1','retained-document') ORDER BY kind,id"))
    await service.close()
    restored = AppService(path, workspace=tmp_path)
    try:
        assert restored._state.get('theme') == (retained if legacy_present else None)
        assert restored.appearance.shared()['values'] == DEFAULTS
        assert restored.appearance.shared()['owner'] != profile['owner']
        assert list(restored.db.execute("SELECT kind,id,value FROM shell_records WHERE id IN ('appearance','reference-v1','shared-reference-v1','appearance-experiment-0801-v1','retained-document') ORDER BY kind,id")) == before
        restored.clients.attach('retained-document')
        with restored.clients.bind('retained-document'):
            snapshot = restored.browser_state()
            assert 'enabled' not in snapshot['appearance']
            assert snapshot['appearance']['effective']['appearanceId'] == 'builtin:default'
            assert snapshot['appearance']['effective']['scheme'] == 'system'
        assert restored.db.execute("SELECT value FROM shell_records WHERE kind='client' AND id='retained-document'").fetchone()[0] == json.dumps(old_client)
        assert (root / 'retained.css').read_bytes() == legacy_css
        assert (root / 'retained.zip').read_bytes() == newer_zip
        from amplifier_web import theme_library
        assert not any(row['id'].startswith('saved:') for row in theme_library.listing(restored)['items'])
        for function in (theme_library.read, theme_library.resolve, theme_library.export):
            with pytest.raises(AppError):
                function(restored, 'saved:retained.zip')
        assert not (path / 'appearance-library-v1').exists()
        import base64
        imported = theme_library.import_appearance(restored, {'packageBase64': base64.b64encode(newer_zip).decode()})
        assert theme_library.read(restored, imported['id'])['name'] == 'Retained delta'
        assert (root / 'retained.zip').read_bytes() == newer_zip
    finally:
        await restored.close()


async def prepare(app, *, presentation=None, remove_chat=False):
    composition = deepcopy(DEFAULT)
    composition['presentation'] = presentation or {}
    if remove_chat:
        composition['instances'] = [row for row in composition['instances'] if row['id'] != 'chats']
    return await call(app, 'shell.changes.prepare', {'clientId': 'one', 'expectedRevision': app.shell.client('one')['revision'],
                                                   'composition': composition})


async def apply(app, prepared, *, action='shell.changes.apply', expected_accepted=True):
    return await call(app, action, {'clientId': 'one', 'expectedRevision': app.shell.client('one')['revision'],
        'changeId': prepared['id']}, expected_accepted=expected_accepted)


async def test_shell_dirty_admission_mixed_storage_activation_and_whole_record_revert(app):
    shared = app.appearance.shared()
    prepared = await prepare(app, presentation={'scheme': 'dark', 'decorations': False}, remove_chat=True)
    client = app.shell.client('one')
    client['views']['chats'] = {'view': {}, 'dirty': True}
    app.shell.put('client', 'one', client)
    assert (await apply(app, prepared, expected_accepted=False))['status'] == 'deferred'
    assert 'command' not in app.appearance.read('one')
    client['views']['chats']['dirty'] = False
    app.shell.put('client', 'one', client)
    await apply(app, prepared)
    assert app.shell.client('one')['revision'] == 0
    assert app.appearance.read('one')['activeScope'] == 'shared'
    await ack(app, status='failed')
    assert app.shell.client('one')['composition'] == DEFAULT
    await apply(app, prepared)
    assert (await ack(app))['activated']
    assert app.shell.client('one')['revision'] == 1
    assert app.shell.inspect('one')['effectiveComposition']['presentation']['scheme'] == 'dark'
    assert app.appearance.shared() == shared
    assert app.appearance.values('two')['scheme'] == 'system'
    await update(app, {'scheme': 'light'})
    await apply(app, prepared, action='shell.changes.revert')
    assert app.shell.client('one')['revision'] == 1
    await ack(app)
    assert app.shell.client('one')['revision'] == 2
    assert app.shell.client('one')['composition'] == DEFAULT
    assert app.appearance.read('one')['activeScope'] == 'shared'
    assert app.appearance.read('one')['local']['values'] is None
    assert app.appearance.values('one')['scheme'] == 'light'


@pytest.mark.parametrize('intervening', ['local', 'shared', 'generation'])
async def test_shell_prepared_presentation_fences_before_queue(app, intervening):
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    if intervening == 'local':
        await initialize(app)
    elif intervening == 'shared':
        await update(app, {'decorations': False})
    else:
        app.appearance.mirrors.pop('one')
        await report(app)
    before = app.appearance.read('one')
    with pytest.raises(AppError, match='Appearance preferences changed'):
        await apply(app, prepared)
    assert app.appearance.read('one') == before and app.shell.client('one')['revision'] == 0


async def test_inactive_global_is_not_shell_fence_while_browser_active(app):
    await initialize(app)
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    assert 'sharedRevision' not in prepared['preferenceFence']
    await update(app, {'decorations': False})
    await apply(app, prepared)
    await update(app, {'scheme': 'light'})
    assert (await ack(app))['activated']
    assert app.appearance.values('one')['decorations'] is True
    assert app.appearance.values('one')['scheme'] == 'dark'


@pytest.mark.parametrize('intervening', ['shared', 'dirty', 'shell'])
async def test_shell_ack_rechecks_selected_seed_and_dirty_fences_without_false_activation(app, intervening):
    prepared = await prepare(app, presentation={'scheme': 'dark'}, remove_chat=True)
    await apply(app, prepared)
    command = deepcopy(app.appearance.read('one')['command'])
    if intervening == 'shared':
        await update(app, {'decorations': False})
    elif intervening == 'dirty':
        client = app.shell.client('one')
        client['views']['chats'] = {'view': {}, 'dirty': True}
        app.shell.put('client', 'one', client)
    else:
        client = app.shell.client('one')
        client['revision'] += 1
        app.shell.put('client', 'one', client)
    before = app.appearance.read('one')
    with pytest.raises(AppError) as error:
        await ack(app)
    assert error.value.status == 409
    assert app.appearance.read('one')['local'] == before['local']
    assert app.appearance.read('one')['command']['status'] == 'pending'
    # Observe persisted storage without retrying activation of the stale shell.
    await report(app, revision=command['expectedRevision'] + 1,
                 storageVersion='persisted-before-rejection', **command['after'])
    assert app.appearance.read('one')['command']['status'] == 'superseded'
    assert app.shell.client('one')['composition'] == DEFAULT


async def test_module_only_preparation_does_not_fence_or_touch_preferences(app):
    prepared = await prepare(app, remove_chat=True)
    assert 'preferenceFence' not in prepared
    await initialize(app)
    await update(app, {'decorations': False})
    await apply(app, prepared)
    assert app.shell.client('one')['revision'] == 1
    assert app.appearance.values('one')['decorations'] is True


async def test_storage_mirror_shell_activation_and_undo_rollback_on_sql_failure(app, monkeypatch):
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    await apply(app, prepared)
    previous = app.appearance.read('one')
    rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    real = app.shell.activate_preference_change
    def broken(identity, change):
        real(identity, change)
        raise RuntimeError('Injected transaction failure')
    monkeypatch.setattr(app.shell, 'activate_preference_change', broken)
    with pytest.raises(RuntimeError, match='Injected'):
        await ack(app)
    assert app.appearance.read('one') == previous
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == rows


async def test_ack_receipt_failure_rolls_back_already_updated_private_mirror_and_undo(app, monkeypatch):
    await select(app)
    previous = app.appearance.read('one')
    rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    token = app.shell.change_token('one')
    real = app.shell.remember_command
    def broken(action, args, origin, command_id, receipt):
        if action == 'appearance.client.report':
            assert app.appearance.read('one')['activeScope'] == 'client'
            raise RuntimeError('Injected receipt write failure')
        return real(action, args, origin, command_id, receipt)
    monkeypatch.setattr(app.shell, 'remember_command', broken)
    with pytest.raises(RuntimeError, match='receipt write'):
        await ack(app)
    assert app.appearance.read('one') == previous
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == rows
    assert app.shell.change_token('one') == token


@pytest.mark.parametrize('public', ['view', 'component'])
async def test_public_client_controls_edit_retained_profile_not_copied_effective_global(app, public):
    await initialize(app)
    await update(app, {'scheme': 'dark', 'decorations': False, 'density': 'compact',
                       'executionVisibility': {'step': False}}, scope='client')
    await ack(app)
    await select(app, 'shared')
    await ack(app)
    await update(app, {'scheme': 'light', 'decorations': True, 'density': 'comfortable'})
    shared = app.appearance.shared()
    local = deepcopy(app.appearance.read('one')['local']['values'])
    if public == 'view':
        await call(app, 'view.update', {'patch': {'scheme': 'system'}})
    else:
        await call(app, 'shell.command', {'clientId': 'one', 'instanceId': 'core.settings.appearance',
            'generation': 0, 'action': 'presentation.update',
            'args': {'expectedRevision': 0, 'patch': {'scheme': 'system'}}})
    assert app.appearance.read('one')['activeScope'] == 'shared'
    assert app.appearance.read('one')['command']['after'] == {'activeScope': 'client', 'values': {**local, 'scheme': 'system'}}
    await ack(app)
    assert app.appearance.shared() == shared
    assert app.appearance.values('one') == {**local, 'scheme': 'system'}
    with app.clients.bind('one'):
        assert app.browser_state()['view']['scheme'] == 'system'


@pytest.mark.parametrize('invalid_patch,match', [
    ({'draft': 42}, 'Draft must be text'),
    ({'draft': 'previous text', 'draftDocument': {}}, 'Invalid quoted document'),
    ({'draft': 'replacement'}, 'Quoted content was kept'),
    ({'draft': 'previous text', 'canvasDraft': 42}, 'Canvas settings must be an object'),
    ({'draft': 'previous text', 'canvasDraft': {'delivery': []}}, 'Canvas delivery must name a destination'),
])
async def test_rejected_mixed_view_action_never_queues_or_mutates(app, invalid_patch, match):
    await call(app, 'view.update', {'patch': {'draft': 'previous text', 'draftDocument': {
        'version': 1, 'segments': [{'type': 'text', 'text': 'previous text'}]}}})
    before = deepcopy(app.clients.records['one'])
    preferences = app.appearance.read('one')
    rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    token = app.shell.change_token('one')
    with pytest.raises(AppError, match=match):
        await call(app, 'view.update', {'patch': {'scheme': 'dark', **invalid_patch}})
    assert app.clients.records['one'] == before
    assert app.appearance.read('one') == preferences
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == rows
    assert app.shell.change_token('one') == token


async def test_valid_mixed_view_action_preserves_prepared_commentary_and_ack(app):
    document = {'version': 1, 'segments': [{'type': 'text', 'text': 'previous text'}]}
    await call(app, 'view.update', {'patch': {'draft': 'previous text', 'draftDocument': document}})
    result = await call(app, 'view.update', {'patch': {'scheme': 'dark', 'draft': 'previous text appended'}})
    assert result['status'] == 'awaiting-storage'
    assert app.clients.records['one']['view']['draft'] == 'previous text appended'
    assert app.clients.records['one']['draftDocuments'][''] == {
        'version': 1, 'segments': [*document['segments'], {'type': 'text', 'text': ' appended'}]}
    assert app.appearance.values('one')['scheme'] == 'system'
    await ack(app)
    assert app.appearance.values('one')['scheme'] == 'dark'


async def test_mixed_view_publisher_failure_cannot_leave_storage_command_or_private_draft(app, monkeypatch):
    await call(app, 'view.update', {'patch': {'draft': 'keep this'}})
    before_view = deepcopy(app.clients.records['one']['view'])
    before = app.appearance.read('one')
    rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    def fail_publish(*args, **kwargs):
        app.db.rollback()
        raise RuntimeError('injected publisher rollback')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_publish_changes', fail_publish)
        patch.setattr(app, '_publish_full', fail_publish)
        with pytest.raises(RuntimeError, match='publisher rollback'):
            await call(app, 'view.update', {'patch': {'scheme': 'dark', 'draft': 'must not land'}},
                       command_id='failed-mixed-action')
    assert app.appearance.read('one') == before
    assert app.clients.records['one']['view'] == before_view
    assert app.clients.records['one']['drafts'][''] == 'keep this'
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == rows
    assert app.db.execute('SELECT 1 FROM commands WHERE id=?', ('failed-mixed-action',)).fetchone() is None
    assert app.shell._pending_notifications == set()


@pytest.mark.parametrize('destination', ['shared', 'client'])
async def test_canvas_approval_after_progress_commit_rolls_back_with_outer_receipt(app, monkeypatch, destination):
    await call(app, 'session.create')
    request_action = 'appearance.preferences.update' if destination == 'shared' else 'appearance.preferences.select'
    manifest = {'version': 1, 'stateSchema': {'type': 'object'}, 'requests': {
        'choose': {'action': request_action, 'schema': {'type': 'object'}}}}
    row = await call(app, 'canvas.apps.create', {'title': 'Appearance approval',
        'content': '<p>Review this appearance choice.</p>', 'manifest': manifest, 'initialState': {}})
    def cas(value):
        return {'id': value['id'], 'sessionId': value['sessionId'],
                'expectedRevision': value['app']['revision'],
                'expectedStateRevision': value['app']['stateRevision']}
    current = app.appearance.read('one')
    payload = ({'scope': 'shared', 'expectedRevision': current['shared']['revision'],
                'patch': {'scheme': 'dark'}} if destination == 'shared' else {
                    **local_cas(app), 'activeScope': 'client',
                    'expectedSharedRevision': current['shared']['revision']})
    row = await call(app, 'canvas.apps.request', {**cas(row), 'name': 'choose', 'input': payload})
    # Resolving a retained request does not require an open Canvas. With no
    # active canvas, remember() need not start an unrelated SQLite write first.
    await call(app, 'canvas.close')
    before = app.appearance.read('one')
    before_app = deepcopy(row['app'])
    before_rows = list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id'))
    token = app.shell.change_token('one')
    revision = app.state['revision']
    flushed, publishing = [], []
    commit_progress = app._commit_pending_progress
    def progress_barrier():
        assert app.shell._transaction_depth == 1
        commit_progress()
        flushed.append(app.state['revision'])
        assert not app.db.in_transaction
    def fail_publish(*args, **kwargs):
        publishing.append(app.db.in_transaction)
        app.db.rollback()
        raise RuntimeError('injected Canvas publisher rollback')
    app._progress_dirty = True
    app._progress_scope_known = True
    app._progress_session_ids = set()
    app._progress_global_keys = set()
    app._progress_record_only = True
    with monkeypatch.context() as patch:
        patch.setattr(app, '_commit_pending_progress', progress_barrier)
        patch.setattr(app, '_publish_changes', fail_publish)
        patch.setattr(app, '_publish_full', fail_publish)
        with pytest.raises(RuntimeError, match='Canvas publisher rollback'):
            await call(app, 'canvas.apps.resolve', {**cas(row), 'approve': True,
                'requestId': row['app']['requests'][-1]['id']},
                command_id='canvas-after-progress', expected_revision=revision + 1)
    assert flushed == [revision + 1]  # Valid post-flush CAS reached approval.
    assert publishing == [True]  # Nested RELEASE did not commit the preference unit.
    assert app.appearance.read('one') == before
    surface = next(item for item in app._state['canvasArtifacts'] if item['id'] == row['id'])
    assert surface['app'] == before_app
    assert list(app.db.execute('SELECT kind,id,value FROM shell_records ORDER BY kind,id')) == before_rows
    assert app.db.execute('SELECT 1 FROM commands WHERE id=?', ('canvas-after-progress',)).fetchone() is None
    assert app.shell._pending_notifications == set()
    assert app.shell.change_token('one') == token


async def test_shell_revert_refuses_intervening_browser_edit(app):
    prepared = await prepare(app, presentation={'scheme': 'dark'})
    await apply(app, prepared)
    await ack(app)
    await update(app, {'decorations': False}, scope='client')
    await ack(app)
    with pytest.raises(AppError, match='Newer browser'):
        await apply(app, prepared, action='shell.changes.revert')
    assert app.appearance.values('one')['decorations'] is False


def test_composition_identity_binds_only_active_source_build_package_and_preview():
    service = SimpleNamespace(installed_build=SimpleNamespace(project=lambda: {'frontend': {'id': 'one'}}))
    theme = {'id': 'saved:look.zip', 'fingerprint': 'same-package'}
    source = {'scope': 'shared', 'revision': 0}
    first = themes.composition(service, theme, source, {'scheme': 'dark'})
    second = themes.composition(service, theme, {**source, 'revision': 1}, {'scheme': 'dark'})
    preview = themes.composition(service, theme, source, {'scheme': 'dark'}, 'preview')
    service.installed_build = SimpleNamespace(project=lambda: {'frontend': {'id': 'two'}})
    third = themes.composition(service, theme, source, {'scheme': 'dark'})
    assert len({first['id'], second['id'], third['id'], preview['id']}) == 4
    assert 'theme.apply' not in ACTION_DEFINITIONS and 'theme.reset' not in ACTION_DEFINITIONS
    assert 'definition' not in str(ACTION_DEFINITIONS['appearance.preview'][1])
    assert 'clear' not in ACTION_DEFINITIONS['appearance.preferences.update'][1]['properties']


async def test_settings_navigation_has_one_appearance_page_and_no_preference_effect(app):
    from amplifier_web.settings_navigation import settings_state
    before = app.appearance.read('one')
    result = await call(app, 'settings.open', {'page': 'appearance', 'surface': 'dialog'})
    assert result['page'] == 'appearance'
    with app.clients.bind('one'):
        navigation = settings_state(app.state)
    assert 'browser-appearance' not in {row['id'] for row in navigation['pages']}
    with pytest.raises(AppError):
        await call(app, 'settings.open', {'page': 'browser-appearance'})
    assert app.appearance.read('one') == before
    await call(app, 'settings.close')
    assert app.appearance.read('one') == before