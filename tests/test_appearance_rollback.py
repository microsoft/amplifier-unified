"""Retired prepared operations stay inert during the production cutover.

Cold legacy/profile/library retention is qualified in test_appearance_preferences;
this file keeps the old prepared-change fence and mixed-view rejection boundary.
"""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from amplifier_web.shell_modules import DEFAULT
from test_appearance_preferences import app, call


@pytest.mark.parametrize('action', ['shell.changes.apply', 'shell.changes.preview', 'shell.changes.revert'])
async def test_retired_profile_prepared_changes_cannot_activate(app, action):
    before = deepcopy(app.shell.client('one'))
    change = {'id': 'old-change', 'clientId': 'one', 'baseRevision': before['revision'],
              'before': deepcopy(DEFAULT), 'composition': deepcopy(DEFAULT),
              'preferenceFence': {'activeScope': 'shared', 'sharedRevision': 1},
              'status': 'prepared'}
    app.shell.put('change', 'old-change', change)
    with pytest.raises(AppError, match='retired appearance'):
        await call(app, action, {'clientId': 'one', 'expectedRevision': before['revision'], 'changeId': 'old-change'})
    assert app.shell.get('change', 'old-change') == change
    assert app.shell.client('one') == before


async def test_invalid_mixed_view_preserves_draft_and_scheme(app):
    await call(app, 'view.update', {'patch': {'draft': 'Keep me'}})
    before = deepcopy(app.appearance.read('one'))
    for invalid in ([], {'delivery': []}):
        with pytest.raises(AppError):
            await call(app, 'view.update', {'patch': {
                'draft': 'Must not land', 'scheme': 'light', 'canvasDraft': invalid}})
        assert app.clients.records['one']['view']['draft'] == 'Keep me'
        assert app.appearance.read('one') == before
