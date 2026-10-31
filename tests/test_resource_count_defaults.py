"""Default admission follows resources, while retained owners and explicit caps survive."""
import json
from contextlib import closing

import pytest

from amplifier_web import task_resources as resources
from amplifier_web import test_execution_settings as settings
from test_configuration_authority import app
from test_task_environments import backend, scope, start
from test_test_queue import queue, submit, drive, record


@pytest.mark.parametrize('route', ['scratch', 'environment', 'queued'])
@pytest.mark.parametrize('explicit_zero', [False, True])
async def test_admission_above_old_owned_counts_preserves_unknown_owners(app, queue, backend, route, explicit_zero):
    sid = scope(app)['sessionId']
    actor = {'sessionId': sid, 'principal': sid}
    template, _ = resources.prepare(app.data_dir, 'workspace', sid, actor,
        {'kind': 'scratch-directory'}, 'retained-original')
    # These are retained ownership records, not 513 physical test environments.
    # Their count exceeded both old defaults (512 total, 32 per chat).
    with closing(resources.connect(app.data_dir)) as db, db:
        for index in range(513):
            resources.save(db, {**template, 'id': f'retained-{index}',
                'commandId': f'retained-command-{index}', 'status': 'unknown'})
        before = db.execute("SELECT id,body FROM allocations ORDER BY id").fetchall()
    if explicit_zero:
        settings.write(app, {'version': 1, 'queue': {}, 'profiles': {},
            'concurrency': {'maxOwnedResources': 0, 'maxOwnedResourcesPerChat': 0}})
    if route == 'scratch':
        row, fresh = resources.prepare(app.data_dir, 'workspace', sid, actor,
            {'kind': 'scratch-directory'}, 'fresh')
        assert fresh and row['status'] == 'prepared'
    elif route == 'environment':
        assert (await start(app))['request']['state'] == 'completed'
    else:
        await submit(app)
        await drive(queue)
        assert record(queue, app)['job']['status'] == 'passed'
    with closing(resources.connect(app.data_dir)) as db:
        assert db.execute("SELECT id,body FROM allocations WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
            (json.dumps([identity for identity, _ in before]),)).fetchall() == before
