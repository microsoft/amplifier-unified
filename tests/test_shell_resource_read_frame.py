"""Direct resource reads share one live overlay without hiding later edits."""
from copy import deepcopy

import pytest

from amplifier_web import history_directory
from test_shell_modules import service
from test_shell_query_owner import prepare, settle


@pytest.mark.parametrize('change', ['title', 'read-receipt'])
async def test_direct_resource_snapshot_observes_fresh_facts_once(service, monkeypatch, change):
    row = service._session('alpha', hydrate=False)
    row['manualUnread'] = 'unread-alpha'
    service._state['attentionRead'] = {}
    owner = prepare(service)
    wanted = ('recentNavigation', 'pinnedNavigation', 'workspaceShortcuts')
    before = await settle(service, wanted=wanted)
    frozen_before = deepcopy(before)
    revision = service._state['revision']
    receipt = service.db.execute(
        "SELECT key,fingerprint FROM history_directory_notices "
        "WHERE owner='alpha' AND key='unread:alpha'").fetchone()
    assert receipt is not None
    key, fingerprint = receipt
    items = before['instances']['chats']['resources']['context']['value']['attention']['items']
    assert next(item for item in items if item['id'] == key)['read'] is False
    original_scalars = history_directory._scalars
    calls, observed_titles = [], []

    def counted(current, *args, **kwargs):
        calls.append(current['id'])
        observed_titles.append((current['id'], current['title']))
        return original_scalars(current, *args, **kwargs)

    monkeypatch.setattr(history_directory, '_scalars', counted)
    if change == 'title':
        row['title'] = 'Renamed without a publication'
    else:
        service._state['attentionRead'][key] = fingerprint

    with service.clients.bind('browser'):
        # This must be the first directory read after the mutation. An
        # unframed oracle first would prepare the facts we intend to test.
        actual = owner.snapshot('browser')
        assert calls == [item['id'] for item in service._state['sessions']]
        assert service.directory._read_frame.get() is None
        if change == 'title':
            assert ('alpha', row['title']) in observed_titles
        else:
            items = actual['instances']['chats']['resources']['context']['value']['attention']['items']
            assert next(item for item in items if item['id'] == key) == {
                'id': key, 'sessionId': 'alpha', 'fingerprint': fingerprint, 'read': True}
        frozen_actual = deepcopy(actual)

        # The original assembly remains the complete unframed value oracle.
        # Re-enter assembly, retaining its ordinary per-resource cache/fences.
        # No await or mutation separates the candidate from this comparison.
        owner.clients['browser'].pop('settled_stamp', None)
        client, composition, instances, bootstrap = owner._prepare('browser', False)
        expected = owner._prepared_snapshot('browser', state=service.state, client=client,
            composition=composition, instances=instances, bootstrap=bootstrap)
        assert expected == frozen_actual
        assert actual == frozen_actual
        assert service.directory._read_frame.get() is None

    # Changed page membership can legitimately remain pending in the first
    # read. Normal query completion, outside the counted comparison, supplies
    # the new title without replacing that earlier immutable envelope.
    after = await settle(service, wanted=wanted)
    if change == 'title':
        page = after['instances']['chats']['resources']['recentNavigation']['value']['recentNavigation']
        assert next(item for item in page['items'] if item['id'] == 'alpha')['title'] == row['title']
    else:
        items = after['instances']['chats']['resources']['context']['value']['attention']['items']
        assert next(item for item in items if item['id'] == key)['read'] is True
    assert service._state['revision'] == revision
    assert before == frozen_before
    assert actual == frozen_actual
