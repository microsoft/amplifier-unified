"""Voice follows the same retained Stop and configuration boundaries as text."""
import copy

import pytest

from amplifier_web.management import Management
from amplifier_web.service import AppError, AppService
from test_assignment_recovery import app, resume, start, stop
from test_collaborative_workspaces import until
from test_service import Runtime


async def test_open_call_cannot_admit_work_after_assignment_stop(app):
    source = app.state['sessions'][0]
    await start(app)
    await stop(app, source)
    before = copy.deepcopy(source)
    with pytest.raises(AppError, match='Resume assignment') as error:
        await app.voice_delegate('Continue the task', 'voice:open-call:blocked', source['id'])
    assert error.value.status == 409 and error.value.code == 'assignment_stopped'
    assert source == before and not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='voice:open-call:blocked'").fetchone()

    await resume(app, source)
    receipt = await app.voice_delegate('Continue the task', 'voice:open-call:resumed', source['id'])
    await until(lambda: len(app.runtime.inputs) == 1)
    assert receipt['accepted'] and app.runtime.inputs[0][1] == 'voice:open-call:resumed'


@pytest.mark.parametrize('pending', [
    {'phase': 'error', 'effect': 'unknown', 'operationId': 'retained', 'requestHash': 'hash'},
    {'phase': 'queued'},
    {'phase': 'error', 'effect': 'none', 'recoveryRequired': True},
])
async def test_voice_waits_for_retained_configuration_recovery(tmp_path, pending):
    runtime = Runtime()
    service = AppService(tmp_path, runtime, workspace=tmp_path)
    await service.dispatch('session.create', {})
    service.management = Management(service)
    session = service._session()
    session.update(configurationBusy=False, pendingConfiguration=pending)
    before = copy.deepcopy(session)
    try:
        with pytest.raises(AppError, match='reconciling conversation settings') as error:
            await service.voice_delegate('Inspect the project', 'voice:open-call:configuration', session['id'])
        assert error.value.status == 409
        assert session == before and not runtime.sent
        assert not service.db.execute("SELECT 1 FROM commands WHERE id='voice:open-call:configuration'").fetchone()
    finally:
        await service.close()
