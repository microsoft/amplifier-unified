"""Existing configuration ownership remains fenced during maintenance."""
import asyncio
import copy
import json

from amplifier_web.management import Management
from amplifier_web.service import AppService
from test_queued_configuration_boundary import boundary, queue, reach_apply, settle


async def test_queued_configuration_waits_without_replay_until_hold_released(boundary):
    b = boundary
    await queue(b)
    before = copy.deepcopy(b.owner['pendingConfiguration'])
    async with b.app.lock:
        b.app.maintenance._hold(epoch='test-maintenance', phase='paused')
        b.app.maintenance._commit()
        b.owner['status'] = 'idle'
    await asyncio.sleep(.65)  # Cross the existing configuration owner's .5s wait.
    assert not b.runtime.calls
    assert b.owner['pendingConfiguration'] == before
    assert b.app.management.maintenance_blockers() == []
    async with b.app.lock:
        b.app.maintenance.value.update(held=False, phase='open')
        b.app.maintenance._commit()
    await reach_apply(b)
    await settle(b)
    assert b.runtime.calls.count('configuration.apply') == 1


async def test_configuration_readiness_uses_dormant_owner_and_loaded_override(tmp_path):
    app = AppService(tmp_path, None, workspace=tmp_path)
    try:
        app.management = Management(app)
        for index in range(150):
            row = {**app._new_session({'title': f'Dormant {index}'}), 'id': f'dormant-{index}', 'status': 'idle'}
            app.db.execute('INSERT OR REPLACE INTO state_records VALUES(?,?,?)',
                           ('session', row['id'], json.dumps(row)))
        historical = {**app._new_session({'title': 'Historical settings'}), 'id': 'z-historical', 'status': 'error', 'configurationBusy': True,
                      'pendingConfiguration': {'phase': 'error', 'recoveryRequired': True}}
        app.db.execute('INSERT OR REPLACE INTO state_records VALUES(?,?,?)',
                       ('session', historical['id'], json.dumps(historical)))
        before = list(app.state['sessions'])
        blocked = app.management.maintenance_blockers()
        assert blocked == [{'kind': 'configuration-unresolved', 'sessionId': historical['id'],
                            'label': 'Earlier conversation settings need inspection; no change is being replayed'}]
        assert app.state['sessions'] == before  # No dormant conversation hydration.
        # A newer admitted owner wins over its old durable projection.
        app.state['sessions'].append({**historical, 'configurationBusy': False})
        assert app.management.maintenance_blockers() == []
        app.state['sessions'][-1].update(configurationBusy=True,
                                       pendingConfiguration={'phase': 'applying', 'effect': 'none'})
        assert app.management.maintenance_blockers()[0]['kind'] == 'configuration-active'
    finally:
        await app.close()
