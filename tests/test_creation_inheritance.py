"""Reviewed inheritance and explicit new-chat choices cannot disagree."""
import copy
import json
import uuid

import pytest

from amplifier_web.service import AppError
from amplifier_web.session_creation import template
from test_live_clients import command, snapshot
from test_schedule_new_tasks import fixture


async def setup(tmp_path, monkeypatch, pinned):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    if not pinned:
        app._session(sid).pop('selection')
        path = app.data_dir / 'sessions' / sid / 'control-state.json'
        state = json.loads(path.read_text())
        state['selection'] = None
        path.write_text(json.dumps(state))
    config, reviewed = template(app, app._session(sid))
    app.clients.attach('web')
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Keep this thought'}})
    await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': 'bm90ZXM='})
    args = {'id': str(uuid.uuid4()), 'workspace': config['workspace'], 'bundle': config['bundle'],
            'inheritConfiguration': {'sessionId': sid, 'configurationHash': reviewed['configurationHash']}}
    return app, runtime, sid, config, args


@pytest.mark.parametrize('pinned', [True, False])
@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_conflicting_inherited_selection_rejected_before_creation(tmp_path, monkeypatch, pinned, origin):
    app, runtime, sid, _, args = await setup(tmp_path, monkeypatch, pinned)
    try:
        args.update(fromDraft=True, selection={'instance': 'synthetic', 'model': 'different-model', 'effort': 'low'})
        before = snapshot(app, 'web')
        source = copy.deepcopy(app._session(sid))
        with pytest.raises(AppError, match='selection must match'):
            if origin == 'ui':
                await command(app, 'web', 'session.create', args, command_id='conflict')
            else:
                await app.app_bridge('dispatch', {'action': 'session.create', 'args': args, 'id': 'conflict'}, sid)
        after = snapshot(app, 'web')
        assert after['selectedSessionId'] == before['selectedSessionId'] is None
        assert after['view']['newSessionDraft'] == before['view']['newSessionDraft']
        assert after['view']['draft'] == before['view']['draft'] == 'Keep this thought'
        assert after['draftAttachments'] == before['draftAttachments']
        assert len(app.state['sessions']) == 1 and app._session(sid) == source
        assert not (app.data_dir / 'sessions' / args['id']).exists()
        assert not app.db.execute('SELECT id FROM commands WHERE id=?', ('conflict',)).fetchone()
        assert not runtime.inputs
    finally:
        await app.close()


@pytest.mark.parametrize('pinned', [True, False])
@pytest.mark.parametrize('from_draft', [True, False])
async def test_matching_or_empty_selection_preserves_creation_contract(tmp_path, monkeypatch, pinned, from_draft):
    app, runtime, sid, config, args = await setup(tmp_path, monkeypatch, pinned)
    try:
        # Empty selection leaves the reviewed bundle default intact. A pinned
        # selection must be exactly the same as the reviewed inherited choice.
        args.update(selection=copy.deepcopy(config['selection']) if pinned else {},
                    fromDraft=from_draft, select=from_draft)
        before = snapshot(app, 'web')
        created = await command(app, 'web', 'session.create', args, command_id='matching')
        target = app._session(created['sessionId'])
        assert target.get('selection') == config['selection']
        assert template(app, target)[1]['configurationHash'] == args['inheritConfiguration']['configurationHash']
        if from_draft:
            assert created['state']['selectedSessionId'] == target['id']
            row = next(row for row in created['state']['sessions'] if row['id'] == target['id'])
            assert row['draft'] == 'Keep this thought' and row['draftAttachments'] == before['draftAttachments']
            assert created['state']['draftAttachments'] == []
            await command(app, 'web', 'session.draft')
            app.clients.attach('reconnected', resume='web')
            duplicate = await command(app, 'reconnected', 'session.create', args, command_id='matching')
        else:
            assert created['state']['selectedSessionId'] is None
            assert created['state']['view']['newSessionDraft'] == before['view']['newSessionDraft']
            assert created['state']['view']['draft'] == before['view']['draft']
            assert created['state']['draftAttachments'] == before['draftAttachments']
            duplicate = await command(app, 'web', 'session.create', args, command_id='matching')
        assert duplicate['duplicate'] and duplicate['sessionId'] == target['id']
        assert duplicate['state']['selectedSessionId'] is None
        assert len(app.state['sessions']) == 2 and not runtime.inputs
        assert app._session(sid)['messages'] == []
    finally:
        await app.close()
