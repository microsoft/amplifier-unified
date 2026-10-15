import pytest
from test_work_windows import fixture


def live_fixture(tmp_path, monkeypatch, count=126):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=0)
    session['execution'].update(nodes=[{'id': str(i), 'turnId': 't', 'kind': 'tool',
        'label': str(i), 'phase': 'completed', 'startedAt': 1, 'endedAt': 2,
        'output': 'body ' * 1000} for i in range(count)],
        turns=[{'id': 't', 'anchorMessageId': 'user', 'phase': 'completed', 'startedAt': 1, 'endedAt': 2}])
    return owner, session, service


def test_explicit_older_steps_and_live_sync_preserve_all_opened_rows_without_full_scan(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    slices = []
    class BoundedRows(list):
        def __iter__(self):
            raise AssertionError('Full execution scan')
        def __getitem__(self, key):
            if isinstance(key, slice):
                start, stop, step = key.indices(len(self))
                assert stop - start <= 100
                slices.append((start, stop))
            return super().__getitem__(key)
    session['execution']['nodes'] = BoundedRows(session['execution']['nodes'])
    first = owner.live_pages.read(session, 't@user')
    assert [row['id'] for row in first['items']] == [str(i) for i in range(26, 126)]
    assert first['before'] and first['total'] is None and not first['coverage']['historyComplete']
    older = owner.live_pages.read(session, 't@user', before=first['before'])
    assert [row['id'] for row in older['items']] == [str(i) for i in range(26)]
    assert older['before'] is None
    field = older['items'][0]['outputDetail']
    assert owner.live_pages.read_text(session, field)['value'] == 'body ' * 1000
    rows = older['items'] + first['items']
    known = {row['id']: row['detailVersion'] for row in rows}
    session['execution']['nodes'][0]['label'] = 'Older changed'
    session['execution']['nodes'][-1]['label'] = 'Latest changed'
    owner.invalidate({'s'})
    synced = owner.live_pages.sync(session, 't@user', known)
    assert synced['order'] == [str(i) for i in range(126)]
    assert [row['id'] for row in synced['items']] == ['0', '125']
    assert synced['before'] is None
    assert len(session['execution']['nodes']) == 126
    assert session['execution']['retiredUsageNodes'] == [{'id': 'protected'}]
    assert slices and all(end - start <= 100 for start, end in slices)


def test_cursor_rejects_mutation_rebind_and_other_group_without_replaying(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    first = owner.live_pages.read(session, 't@user')
    with pytest.raises(ValueError):
        owner.live_pages.read(session, 'other@user', before=first['before'])
    with pytest.raises(ValueError):
        owner.live_pages.read(session, 't@user', before=first['before'][:-4] + 'aaaa')
    owner.invalidate({'s'})
    with pytest.raises(ValueError, match='changed'):
        owner.live_pages.read(session, 't@user', before=first['before'])
    first = owner.live_pages.read(session, 't@user')
    session['nativeIdentity'] = 'replacement'
    with pytest.raises(ValueError, match='expired'):
        owner.live_pages.read(session, 't@user', before=first['before'])
    with pytest.raises(ValueError, match='expired'):
        owner.live_pages.read_text(session, first['items'][0]['outputDetail'])


def test_empty_filtered_ranges_progress_and_never_promote_native_rows(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=250)
    for i in range(150):
        session['execution']['nodes'][i]['canonicalHistory'] = True
    first = owner.live_pages.read(session, 't@user')
    assert len(first['items']) == 100
    older = owner.live_pages.read(session, 't@user', before=first['before'])
    assert older['items'] == [] and older['before']
    final = owner.live_pages.read(session, 't@user', before=older['before'])
    assert final['items'] == [] and final['before'] is None
    assert not final['coverage']['historyComplete']


def test_receipt_budget_refuses_next_page_without_discarding_opened_steps(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=901)
    page = owner.live_pages.read(session, 't@user')
    for _ in range(7):
        page = owner.live_pages.read(session, 't@user', before=page['before'])
    entry = owner.live_pages.groups[(None, 's', 't@user')]
    assert len(entry['ids']) == 800 and len(entry['ranges']) == 8
    before = dict(entry['ids'])
    with pytest.raises(ValueError, match='page limit'):
        owner.live_pages.read(session, 't@user', before=page['before'])
    assert entry['ids'] == before and page['before']
    owner.retain_interest({'s'})
    owner.retain_interest(set())
    assert not owner.live_pages.groups


@pytest.mark.asyncio
async def test_http_live_steps_offer_older_page_and_sync_preserves_126(authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
        voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    _, source, _ = live_fixture(tmp_path, monkeypatch)
    session.update(messages=source['messages'], execution=source['execution'])
    service.clients.attach('live-work-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'live-work-browser'
    original = service._session
    def bounded(identity=None, *, hydrate=True):
        assert hydrate is False
        return original(identity, hydrate=False)
    monkeypatch.setattr(service, '_session', bounded)
    query = {'sessionId': session['id'], 'part': 'nodes', 'group': 't@user'}
    response = await client.get('/api/conversation/detail', params=query)
    assert response.status == 200, await response.text()
    first = await response.json()
    assert len(first['items']) == 100 and first['before']
    older = await (await client.get('/api/conversation/detail', params={**query, 'before': first['before']})).json()
    assert len(older['items']) == 26 and older['before'] is None
    # A second browser opens the same group without discarding this reader's
    # explicit older-page ownership or gaining access to its cursor/field.
    service.clients.attach('other-work-browser')
    client.session.headers['X-Amplifier-Client'] = 'other-work-browser'
    second = await (await client.get('/api/conversation/detail', params=query)).json()
    assert len(second['items']) == 100
    forbidden = await client.get('/api/conversation/detail', params={**query, 'before': first['before']})
    assert forbidden.status == 400
    forbidden = await client.get('/api/conversation/detail', params=older['items'][0]['outputDetail'])
    assert forbidden.status == 400
    client.session.headers['X-Amplifier-Client'] = 'live-work-browser'
    field = older['items'][0]['outputDetail']
    result = await (await client.get('/api/conversation/detail', params=field)).json()
    assert result['value'] == 'body ' * 1000
    known = {row['id']: row['detailVersion'] for row in older['items'] + first['items']}
    session['execution']['nodes'][0]['label'] = 'Changed earlier'
    service.event_log_view.windows.invalidate({session['id']})
    result = await (await client.post('/api/conversation/work-sync', json={
        'sessionId': session['id'], 'group': 't@user', 'known': known})).json()
    assert result['order'] == [str(i) for i in range(126)]
    assert [row['id'] for row in result['items']] == ['0']
    monkeypatch.setattr(service, '_session', original)


def test_sync_updates_revision_and_preserves_explicit_older_cursor(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    first = owner.live_pages.read(session, 't@user')
    known = {row['id']: row['detailVersion'] for row in first['items']}
    session['execution']['nodes'][-1]['label'] = 'Changed latest'
    session['execution']['nodes'].append({**session['execution']['nodes'][-1], 'id': '126'})
    owner.invalidate({'s'})
    synced = owner.live_pages.sync(session, 't@user', known)
    assert synced['revision'] != first['revision']
    assert synced['order'] == [str(i) for i in range(26, 127)]
    older = owner.live_pages.read(session, 't@user', before=synced['before'], revision=synced['revision'])
    assert [row['id'] for row in older['items']] == [str(i) for i in range(26)]
    assert older['before'] is None


def test_small_live_appends_coalesce_receipts_instead_of_spending_one_per_revision(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    first = owner.live_pages.read(session, 't@user')
    known = {row['id']: row['detailVersion'] for row in first['items']}
    for identity in range(126, 151):
        session['execution']['nodes'].append({**session['execution']['nodes'][-1], 'id': str(identity)})
        owner.invalidate({'s'})
        result = owner.live_pages.sync(session, 't@user', known)
        known.update((row['id'], row['detailVersion']) for row in result['items'])
    assert result['order'] == [str(i) for i in range(26, 151)]
    assert len(owner.live_pages.groups[(None, 's', 't@user')]['ranges']) == 2
    older = owner.live_pages.read(session, 't@user', before=result['before'], revision=result['revision'])
    assert [row['id'] for row in older['items']] == [str(i) for i in range(26)]
