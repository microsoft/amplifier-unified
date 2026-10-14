import pytest

from test_client_observations import observation_host, replace_catalog


@pytest.mark.parametrize('indexed', [True, False])
async def test_root_catalog_filters_before_paging_without_history(observation_host, monkeypatch, indexed):
    app, client = observation_host
    service = app['service']
    workspace = service.default_workspace
    rows = [dict(id=f'child{i}', sessionKind='worker', parentId='first', workspace=workspace)
            for i in range(12)]
    rows += [dict(id='first', title='First', workspace=workspace),
             dict(id='internal', sessionKind='internal', workspace=workspace),
             dict(id='foreign', sessionKind='root', workspace=workspace + '/other'),
             dict(id='second', title='Second', sessionKind='root', workspace=workspace)]
    replace_catalog(service, rows)
    original_directory = service.directory
    if not indexed:
        monkeypatch.setattr(service, 'directory', None)

    async def forbidden(*args, **kwargs):
        raise AssertionError('Catalog discovery must not load transcript or start a runtime')

    monkeypatch.setattr(service.history, 'ensure_loaded', forbidden)
    monkeypatch.setattr(service.runtime, 'start', forbidden)
    decoded = []
    if indexed:
        original = service.directory.decode
        def observe(row):
            decoded.append(row[0])
            return original(row)
        monkeypatch.setattr(service.directory, 'decode', observe)
    try:
        params = dict(kind='root', workspace=workspace, limit=1)
        response = await client.get('/api/sessions', params=params)
        assert response.status == 200
        first = await response.json()
        response = await client.get('/api/sessions', params={**params, 'offset': first['nextOffset']})
        second = await response.json()
        assert [row['id'] for row in first['items'] + second['items']] == ['first', 'second']
        assert first['nextOffset'] == 1 and second['nextOffset'] is None
        if indexed:
            assert set(decoded) == {'first', 'second'} and len(decoded) <= 3
        response = await client.get('/api/sessions', params={'workspace': workspace, 'limit': 100})
        assert len((await response.json())['items']) == 15  # Unfiltered contract unchanged.
    finally:
        service.directory = original_directory



@pytest.mark.parametrize('kind', ['', 'worker', 'all', "root' OR 1=1"])
async def test_root_catalog_rejects_unsupported_kind(observation_host, kind):
    _, client = observation_host
    response = await client.get('/api/sessions', params={'kind': kind})
    assert response.status == 400
