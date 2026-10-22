"""Model discovery must not outlive newer, correctly owned readiness facts."""
import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.conversation_models import browse, select
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


@pytest.fixture
async def app(tmp_path, monkeypatch):
    service = AppService(tmp_path/'app', workspace=tmp_path)
    await service.dispatch('session.create', {'title': 'Fresh model'})
    service.management = Management(service)
    async def defaults(*args, **kwargs):
        return {'providers': [{'id': 'fixture'}],
                'effective': {'instance': 'fixture', 'model': 'initial'}, 'catalogs': {}}
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', defaults)
    yield service
    await service.close()


def catalog(app):
    return app.state['runtimeControl'][app._session()['id']]['configuration.catalog']


async def report(app, model='reported', **provenance):
    await app.on_runtime_event('runtime.status', {'sessionId': app._session()['id'],
        'status': 'ready', 'report': {'effective_selection': {'instance': 'fixture', 'model': model}},
        **provenance})


async def test_report_refreshes_existing_catalog_and_later_browse(app):
    await browse(app.management, app._session())
    assert catalog(app)['effective']['model'] == 'initial'
    await report(app)
    assert catalog(app)['effective']['model'] == 'reported'
    await browse(app.management, app._session(), refresh=True)
    assert catalog(app)['effective']['model'] == 'reported'
    assert not app._session().get('selection')
    assert app.runtime is None


async def test_report_arriving_during_catalog_read_is_rechecked_at_commit(app, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    async def defaults(*args, **kwargs):
        entered.set(); await finish.wait()
        return {'providers': [{'id': 'fixture'}],
                'effective': {'instance': 'fixture', 'model': 'older-discovery'}, 'catalogs': {}}
    monkeypatch.setattr('amplifier_web.draft_defaults.resolve_defaults', defaults)
    task = asyncio.create_task(browse(app.management, app._session()))
    await entered.wait()
    await report(app)
    finish.set(); await task
    assert catalog(app)['effective']['model'] == 'reported'


async def test_pending_and_saved_explicit_selection_outrank_report(app):
    await browse(app.management, app._session())
    wanted = {'instance': 'fixture', 'model': 'next-input', 'effort': 'high'}
    await select(app.management, app._session(), wanted)
    await report(app)
    assert catalog(app)['effective'] == wanted
    assert app._session()['pendingModelSelection'] == wanted
    app._session().pop('pendingModelSelection')
    await report(app, 'later-report')
    await browse(app.management, app._session(), refresh=True)
    assert catalog(app)['effective'] == wanted


@pytest.mark.parametrize('obsolete', ['revision', 'pending', 'busy'])
async def test_old_or_reconciling_report_cannot_override_new_settings_catalog(app, obsolete):
    app.state['configurationRevision'] = 2
    await browse(app.management, app._session())
    if obsolete == 'pending': app._session()['configurationPending'] = True
    if obsolete == 'busy': app._session()['configurationBusy'] = True
    await report(app, 'old-worker', configurationRevision=1 if obsolete == 'revision' else 2)
    assert catalog(app)['effective']['model'] == 'initial'
    await browse(app.management, app._session(), refresh=True)
    assert catalog(app)['effective']['model'] == 'initial'


async def test_fresh_report_at_nonzero_revision_refreshes_catalog(app):
    app.state['configurationRevision'] = 2
    await browse(app.management, app._session())
    await report(app, configurationRevision=2)
    assert catalog(app)['effective']['model'] == 'reported'


@pytest.mark.parametrize('owner,closing,returncode', [('old-worker', False, None), ('current', True, None), ('current', False, 0)])
async def test_replaced_or_closing_worker_report_is_not_adopted(app, owner, closing, returncode):
    await browse(app.management, app._session())
    await report(app, 'accepted')
    before = deepcopy(app._session()['runtimeReport'])
    runtime = SimpleNamespace(workers={app._session()['id']: {
        'runtimeOwnerId': 'current', 'closing': closing,
        'process': SimpleNamespace(returncode=returncode)}})
    app.runtime = runtime
    try:
        await report(app, 'obsolete', runtimeOwnerId=owner, configurationRevision=0)
        assert app._session()['runtimeReport'] == before
        assert catalog(app)['effective']['model'] == 'accepted'
    finally:
        app.runtime = None


async def test_warmup_uses_same_report_owner_without_clearing_stopped_turn(app):
    await browse(app.management, app._session())
    app._session()['status'] = 'stopped'
    class Runtime:
        async def prewarm(self, source, emit):
            await emit('runtime.status', {'sessionId': source['id'], 'status': 'ready',
                'report': {'effective_selection': {'instance': 'fixture', 'model': 'warm'}}})
    app.runtime = Runtime()
    try:
        await app.warmup.run(app._session()['id'])
        assert catalog(app)['effective']['model'] == 'warm'
        assert app._session()['status'] == 'stopped'
    finally:
        app.runtime = None


@pytest.mark.parametrize('change_settings', [False, True])
async def test_real_process_report_revision_is_captured_before_startup_wait(app, change_settings):
    script = "import json,sys;sys.stdin.readline();print(json.dumps({'type':'runtime.ready','report':{'effective_selection':{'instance':'fixture','model':'old-start'}}}),flush=True);sys.stdin.readline()"
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', script], startup_timeout=2)
    await app.install_runtime(manager)
    started, release, reported = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []
    async def emit(kind, payload):
        if kind == 'runtime.status' and payload.get('status') == 'starting':
            started.set(); await release.wait()
        if payload.get('report'): observed.append(deepcopy(payload))
        await app.on_runtime_event(kind, payload)
        if payload.get('report'): reported.set()
    task = asyncio.create_task(manager.start(app._session(), emit))
    try:
        await started.wait()
        if change_settings: app.state['configurationRevision'] = 1
        await browse(app.management, app._session())
        release.set(); await task
        await asyncio.wait_for(reported.wait(), 2)
        assert observed[0]['configurationRevision'] == 0
        assert observed[0]['runtimeOwnerId'] == manager.workers[app._session()['id']]['runtimeOwnerId']
        assert catalog(app)['effective']['model'] == ('initial' if change_settings else 'old-start')
    finally:
        release.set()
        await manager.close()
