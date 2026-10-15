"""A new catalog generation is not a replay of a failed shell read."""
from copy import deepcopy
import time

import pytest

from amplifier_web.service import AppError
from amplifier_web.shell_resources import page_value
from test_shell_modules import service
from test_shell_query_owner import prepare, snapshot, settle


async def test_workspace_change_admits_new_keys_without_replaying_failed_generation(service):
    owner = prepare(service)
    reads = []
    factory = owner.queries.factory

    def fail_first_read(*args, **kwargs):
        projector = factory(*args, **kwargs)
        project = projector.project

        async def controlled(inputs, **options):
            key = options['key']
            reads.append(key)
            if reads.count(key) == 1:
                return {'status': 'retry', 'reason': 'worker_failed'}
            return await project(inputs, **options)

        projector.project = controlled
        return projector

    owner.queries.factory = fail_first_read
    snapshot(service, wanted=('sidebarSearch',))
    await owner.queries.runner
    first = snapshot(service, wanted=('sidebarSearch',))
    assert all(row['pending']['sidebarSearch']['status'] == 'retry' for row in first['instances'].values())
    frozen = deepcopy(first)
    failed_keys = set(owner.queries.failures)
    failed_reads = list(reads)
    before = owner.epochs['workspaces']

    # A real canonical registry write is a different data generation under the
    # same registered client/view. No resource POST or explicit Retry is needed.
    service.state['workspaces'][0]['name'] = 'Renamed registry workspace'
    service._publish_changes(globals={'workspaces'})
    assert owner.epochs['workspaces'] == before + 1
    snapshot(service, wanted=('sidebarSearch',))
    await owner.queries.runner
    changed = snapshot(service, wanted=('sidebarSearch',))
    assert all(row['pending']['sidebarSearch']['status'] == 'retry' for row in changed['instances'].values())
    current = {key for key, failure in owner.queries.failures.items()
               if any(owner.queries._current(item) for item in failure['interests'].values())}
    assert current and current.isdisjoint(failed_keys)
    assert len(reads) == 2 * len(failed_reads) and len(set(reads)) == len(reads)
    assert failed_keys <= owner.queries.failures.keys()
    assert not owner.queries.retries  # worker failures do not get the source-change retry
    with pytest.raises(AppError) as refused:
        owner.queries.retry('browser')
    assert refused.value.status == 429

    # Time alone cannot replay either failed generation. The explicit retry
    # addresses only current interests, then uses the real read-only worker.
    for failure in owner.queries.failures.values():
        failure['until'] = time.monotonic() - 1
    assert snapshot(service, wanted=('sidebarSearch',)) == changed
    assert len(reads) == 2 * len(failed_reads) and owner.queries.runner is None
    owner.queries.retry('browser')
    ready = await settle(service, wanted=('sidebarSearch',))
    assert all(reads.count(key) == 1 for key in failed_reads)
    assert all(reads.count(key) == 2 for key in set(reads) - set(failed_reads))
    assert first == frozen
    with service.clients.bind('browser'):
        from amplifier_web.shell_components import resolved
        client = service.shell.client('browser')
        for instance in resolved(client['composition']):
            if instance['id'] in ready['instances']:
                expected = service.shell.navigation(client, instance, identity='browser', navigation_resources={'sidebarSearch'})
                assert ready['instances'][instance['id']]['resources']['sidebarSearch']['value'] == page_value(expected, 'sidebarSearch')
