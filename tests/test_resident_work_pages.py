import pytest
from test_work_windows import fixture
from test_work_windows import work_failure_observation  # noqa: F401 - scoped observer fixture
from test_cold_work_binding import source as canonical_source  # noqa: F401 - shared pytest fixture


def live_fixture(tmp_path, monkeypatch, count=126):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=0)
    session['execution'].update(nodes=[{'id': str(i), 'turnId': 't', 'kind': 'tool',
        'label': str(i), 'phase': 'completed', 'startedAt': 1, 'endedAt': 2,
        'output': 'body ' * 1000} for i in range(count)],
        turns=[{'id': 't', 'anchorMessageId': 'user', 'phase': 'completed', 'startedAt': 1, 'endedAt': 2}])
    return owner, session, service


@pytest.mark.parametrize('shape,expected', [
    ('inline', False), ('metadata', True), ('foreign-group', False),
    ('canonical-alias', True), ('mixed', True),
])
def test_latest_resident_requirement_is_bounded_and_never_resolves_proof(tmp_path, monkeypatch, shape, expected):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    nodes = session['execution']['nodes']
    if shape in {'metadata', 'canonical-alias', 'mixed'}:
        selected = nodes if shape != 'mixed' else nodes[-1:]
        for row in selected:
            row.pop('output')
            row['liveObservation'] = True
            if shape == 'canonical-alias':
                row['_eventFields'] = {'output': {'untrusted': 'not-inline'}}
    elif shape == 'foreign-group':
        nodes.append({'id': 'foreign', 'turnId': 'other', 'kind': 'tool',
                      'liveObservation': True, 'startedAt': 1, 'phase': 'completed'})
    class Bounded(list):
        def __iter__(self):
            raise AssertionError('Requirement classification scanned the whole resident graph')
        def __getitem__(self, key):
            if isinstance(key, slice):
                start, end, _ = key.indices(len(self))
                assert end - start <= 100
            return super().__getitem__(key)
    session['execution']['nodes'] = Bounded(nodes)
    def forbidden(*args, **kwargs):
        raise AssertionError('Requirement classification read canonical evidence')
    monkeypatch.setattr(owner, 'live_binding', forbidden)
    monkeypatch.setattr(owner, '_worker', forbidden)
    assert owner.live_pages.needs_canonical(session, 't@user') is expected


@pytest.mark.asyncio
async def test_registered_inline_latest_uses_no_writer_or_canonical_read(
        authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'inline-only', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    _, source, _ = live_fixture(tmp_path, monkeypatch)
    session.update(messages=source['messages'], execution=source['execution'])
    service.clients.attach('inline-browser', kind='web')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'inline-browser'
    windows = service.event_log_view.windows
    original = service._session
    def resident(identity=None, *, hydrate=True):
        assert hydrate is False
        return original(identity, hydrate=False)
    async def forbidden(*args, **kwargs):
        raise AssertionError('Inline-only latest Work requested canonical proof')
    monkeypatch.setattr(service, '_session', resident)
    monkeypatch.setattr(windows, 'read_selected', forbidden)
    try:
        response = await client.get('/api/conversation/detail', params={
            'sessionId': session['id'], 'part': 'nodes', 'group': 't@user'})
        assert response.status == 200, await response.text()
        page = await response.json()
        assert len(page['items']) == 100 and page['before']
        assert not windows._root_proofs
        assert all(row['outputDetail']['windowKey'].startswith('resident-work:') for row in page['items'])
    finally:
        monkeypatch.setattr(service, '_session', original)
        await client.close()


@pytest.mark.asyncio
async def test_explicit_older_steps_and_live_sync_preserve_all_opened_rows_without_full_scan(tmp_path, monkeypatch):
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
    assert (await owner.live_pages.read_text(session, field))['value'] == 'body ' * 1000
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


@pytest.mark.asyncio
async def test_cursor_rejects_mutation_rebind_and_other_group_without_replaying(tmp_path, monkeypatch):
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
        await owner.live_pages.read_text(session, first['items'][0]['outputDetail'])


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
    result = {'order': [], 'items': []}
    revision = None
    entries = list(known.items())
    for start in range(0, len(entries), 100):
        response = await client.post('/api/conversation/work-sync', json={
            'sessionId': session['id'], 'group': 't@user', 'paged': True,
            'known': dict(entries[start:start + 100]),
            **({'revision': revision} if revision is not None else {})})
        assert response.status == 200, await response.text()
        chunk = await response.json()
        if revision is None:
            revision = chunk['revision']
        assert chunk['revision'] == revision and len(chunk['order']) <= 100
        result['order'].extend(chunk['order'])
        result['items'].extend(chunk['items'])
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


@pytest.mark.parametrize('change', ['completed-reconstruction', 'assistant-append'])
@pytest.mark.asyncio
async def test_open_steps_follow_completion_and_message_append(tmp_path, monkeypatch, change):
    import copy
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=2)
    node = session['execution']['nodes'][-1]
    node.update(kind='llm', phase='running', endedAt=None)
    first = owner.live_pages.read(session, 't@user')
    known = {row['id']: row['detailVersion'] for row in first['items']}
    if change == 'completed-reconstruction':
        # Canonical reconciliation may replace an equivalent resident list.
        session['execution'] = copy.deepcopy(session['execution'])
    else:
        session['messages'].append({'id': 'answer', 'role': 'assistant',
                                    'text': 'Done', 'createdAt': 3})
    session['execution']['nodes'][-1].update(phase='completed', endedAt=2,
        usage={'inputTokens': 100, 'outputTokens': 20, 'totalTokens': 120})
    owner.invalidate({'s'})
    synced = owner.live_pages.sync(session, 't@user', known)
    assert synced['order'] == ['0', '1']
    assert synced['items'][-1]['phase'] == 'completed'
    assert synced['items'][-1]['usage']['totalTokens'] == 120
    assert (await owner.live_pages.read_text(session, first['items'][0]['outputDetail']))['value'] == 'body ' * 1000


@pytest.mark.parametrize('change', ['reorder', 'truncate', 'replace-id', 'reanchor', 'source-rebind', 'session-replaced'])
def test_live_sync_rejects_changed_admitted_positions_or_group(tmp_path, monkeypatch, change):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=2)
    first = owner.live_pages.read(session, 't@user')
    known = {row['id']: row['detailVersion'] for row in first['items']}
    if change == 'reorder':
        session['execution']['nodes'].reverse()
    elif change == 'truncate':
        session['execution']['nodes'].pop(0)
    elif change == 'replace-id':
        session['execution']['nodes'][0]['id'] = 'replacement'
    elif change == 'reanchor':
        session['execution']['nodes'][0]['anchorMessageId'] = 'different'
    elif change == 'session-replaced':
        session = dict(session)
    else:
        session['nativeIdentity'] = 'different'
    owner.invalidate({'s'})
    with pytest.raises(ValueError, match='changed|expired'):
        owner.live_pages.sync(session, 't@user', known)


def test_reconciled_open_window_reissues_strict_older_cursor(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    first = owner.live_pages.read(session, 't@user')
    known = {row['id']: row['detailVersion'] for row in first['items']}
    session['execution']['nodes'] = list(session['execution']['nodes'])
    session['messages'].append({'id': 'answer', 'role': 'assistant', 'createdAt': 3})
    owner.invalidate({'s'})
    with pytest.raises(ValueError, match='changed|expired'):
        owner.live_pages.read(session, 't@user', before=first['before'])
    synced = owner.live_pages.sync(session, 't@user', known)
    assert synced['order'] == [str(i) for i in range(26, 126)]
    with pytest.raises(ValueError, match='changed|expired'):
        owner.live_pages.read(session, 't@user', before=first['before'])
    older = owner.live_pages.read(session, 't@user', before=synced['before'], revision=synced['revision'])
    assert [row['id'] for row in older['items']] == [str(i) for i in range(26)]


def proven_live_fixture(tmp_path, monkeypatch):
    """Consumer contract double, not evidence that the producer proves identity."""
    import copy
    from amplifier_web.browser_detail import digest
    owner, session, service = live_fixture(tmp_path, monkeypatch, count=1)
    node = session['execution']['nodes'][0]
    node.pop('output')
    node.update(rootSessionId='s', sessionId='s', revision=2, liveObservation=True)
    original = copy.deepcopy(node)
    text = 'canonical \u03bb\U0001f680\n' * 2000
    state = {'id': 'proof-one', 'status': 'complete', 'generation': 'generation-one'}
    bound = {**copy.deepcopy(node), 'id': 'raw-occurrence:17',
             '_eventFields': {'output': {'offset': 17, 'length': 80, 'sha256': 'line-one'}},
             'outputDetail': {'sessionId': 's', 'part': 'nodes', 'id': 'raw-occurrence:17',
                              'field': 'output', 'digest': digest(text), 'length': len(text)}}
    calls = []
    def status(identity, session=None, **kwargs):
        assert identity == 's'
        return dict(state)
    def binding(current, row, proof_id=None):
        assert current is session
        # The matcher receives the actual resident row, not a bound copy or a
        # synthesized delegate row. The producer owns authenticating these facts.
        assert row is session['execution']['nodes'][0]
        calls.append(('binding', proof_id))
        fields = ('rootSessionId', 'sessionId', 'id', 'kind', 'turnId', 'parentId',
                  'revision', 'phase', 'startedAt', 'endedAt')
        if (state['status'] != 'complete' or proof_id not in (None, state['id'])
                or any(row.get(key) != original.get(key) for key in fields)):
            return None, None
        return state['id'], copy.deepcopy(bound)
    async def field(identity, proof_id, bound_id, args):
        calls.append(('field', identity, proof_id, bound_id, dict(args)))
        assert identity == 's' and proof_id == state['id'] and state['status'] == 'complete'
        assert bound_id == 'raw-occurrence:17'
        assert args['digest'] == bound['outputDetail']['digest']
        offset = int(args.get('offset', 0))
        end = min(len(text), offset + 16000)
        return {'value': text[offset:end], 'nextOffset': end if end < len(text) else None, 'total': len(text)}
    def forbidden(*args, **kwargs):
        raise AssertionError('A resident consumer must not start a proof or scan')
    monkeypatch.setattr(owner, '_proof_state', status)
    monkeypatch.setattr(owner, 'live_binding', binding, raising=False)
    monkeypatch.setattr(owner, 'read_proven_field', field, raising=False)
    monkeypatch.setattr(owner, 'prove', forbidden)
    monkeypatch.setattr(owner, 'request', forbidden)
    return owner, session, service, state, bound, calls, text


@pytest.mark.asyncio
async def test_proven_live_fields_are_ephemeral_and_route_raw_occurrence_without_body_cache(tmp_path, monkeypatch):
    import copy
    owner, session, _, _, _, calls, text = proven_live_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(session)
    page = owner.live_pages.read(session, 't@user')
    row = page['items'][0]
    assert row['id'] == '0' and 'output' not in row and '_eventFields' not in row
    field = row['outputDetail']
    assert field['id'] == '0' and field['windowKey'].startswith('resident-work:')
    assert field['detailVersion'] == row['detailVersion']
    pieces, offset = [], 0
    while offset is not None:
        result = await owner.live_pages.read_text(session, {**field, 'offset': offset})
        pieces.append(result['value'])
        assert result['total'] == len(text)
        offset = result['nextOffset']
    assert ''.join(pieces) == text
    assert all(call[3] == 'raw-occurrence:17' for call in calls if call[0] == 'field')
    assert session == original
    retained = repr(owner.live_pages.groups)
    assert text not in retained and 'canonical ' not in retained
    assert len(owner.live_pages.groups[(None, 's', 't@user')]['ids']) == 1


@pytest.mark.parametrize('key,value', [
    ('rootSessionId', 'other'), ('sessionId', 'other'), ('id', 'other'),
    ('kind', 'llm'), ('turnId', 'other'), ('parentId', 'other'), ('revision', 3),
    ('phase', 'running'), ('startedAt', 0.5), ('endedAt', 3),
])
def test_live_consumer_does_not_bypass_full_identity_matcher(tmp_path, monkeypatch, key, value):
    owner, session, _, _, _, calls, _ = proven_live_fixture(tmp_path, monkeypatch)
    session['execution']['nodes'][0][key] = value
    group = 'other@user' if key == 'turnId' else 't@user'
    page = owner.live_pages.read(session, group)
    assert calls and all(call[0] == 'binding' for call in calls)
    assert 'outputDetail' not in page['items'][0]


@pytest.mark.parametrize('change', ['proof-id', 'reference', 'digest', 'stale'])
@pytest.mark.asyncio
async def test_reference_only_sync_changes_versions_and_expires_old_fields(tmp_path, monkeypatch, change):
    owner, session, _, state, bound, _, _ = proven_live_fixture(tmp_path, monkeypatch)
    page = owner.live_pages.read(session, 't@user')
    old = page['items'][0]
    if change == 'proof-id':
        state['id'] = 'proof-two'
    elif change == 'reference':
        bound['_eventFields']['output']['sha256'] = 'line-two'
    elif change == 'digest':
        bound['outputDetail']['digest'] = 'new-digest'
    else:
        state['status'] = 'stale'
    synced = owner.live_pages.sync(session, 't@user', {'0': old['detailVersion']}, paged=True)
    assert synced['order'] == ['0'] and len(synced['items']) == 1
    assert synced['items'][0]['detailVersion'] != old['detailVersion']
    if change == 'stale':
        assert 'outputDetail' not in synced['items'][0]
    else:
        assert synced['items'][0]['outputDetail']['detailVersion'] == synced['items'][0]['detailVersion']
    with pytest.raises(ValueError):
        await owner.live_pages.read_text(session, old['outputDetail'])


def test_revision_for_includes_proof_state_without_creating_proof_and_accepts_projection_revision(tmp_path, monkeypatch):
    owner, session, _, state, _, _, _ = proven_live_fixture(tmp_path, monkeypatch)
    from amplifier_web.browser_detail import page as detail_page
    base = detail_page(owner.live_view(session), 'groups')['items'][0]['detailRevision']
    revision = owner.live_pages.revision_for(session, 't@user', base)
    result = owner.live_pages.read(session, 't@user', revision=revision)
    assert result['revision'] == revision
    state['status'] = 'stale'
    stale = owner.live_pages.revision_for(session, 't@user', base)
    assert stale != revision
    with pytest.raises(ValueError):
        owner.live_pages.read(session, 't@user', revision=revision)
    assert owner.live_pages.read(session, 't@user', revision=stale)['revision'] == stale
    monkeypatch.setattr(owner, '_proof_state', lambda identity, session=None, **kwargs: None)
    assert owner.live_pages.revision_for(session, 't@user', base) == base
    assert owner.live_pages.read(session, 't@user', revision=base)['revision'] == base


def test_latest_preserve_and_paged_sync_keep_other_chunks_and_do_not_add_tail(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=226)
    first = owner.live_pages.read(session, 't@user')
    middle = owner.live_pages.read(session, 't@user', before=first['before'])
    older = owner.live_pages.read(session, 't@user', before=middle['before'])
    prior = owner.live_pages.groups[(None, 's', 't@user')]
    key = prior['key']
    session['execution']['nodes'].append({**session['execution']['nodes'][-1], 'id': '226'})
    owner.invalidate({'s'})
    known = {row['id']: row['detailVersion'] for row in older['items']}
    result = owner.live_pages.sync(session, 't@user', known, paged=True)
    assert result['order'] == [str(i) for i in range(26)] and not result['items']
    assert len(prior['ids']) == 226 and '226' not in prior['ids']
    latest = owner.live_pages.read(session, 't@user', preserve=True)
    current = owner.live_pages.groups[(None, 's', 't@user')]
    assert current['key'] == key and len(current['ids']) == 227
    assert latest['before'] is None
    assert set(known) <= current['ids'].keys()
    with pytest.raises(ValueError):
        owner.live_pages.sync(session, 't@user', {str(i): 'old' for i in range(101)}, paged=True)
    with pytest.raises(ValueError):
        owner.live_pages.sync(session, 't@user', known, paged=True, revision='wrong')


@pytest.mark.parametrize('change', ['client', 'reopen', 'evict', 'owner', 'source', 'epoch',
                                    'position', 'group', 'phase', 'revision', 'proof', 'reference'])
@pytest.mark.asyncio
async def test_live_field_rechecks_admission_after_await(tmp_path, monkeypatch, change):
    import asyncio
    owner, session, service, state, bound, calls, _ = proven_live_fixture(tmp_path, monkeypatch)
    pages = owner.live_pages
    page = pages.read(session, 't@user')
    args = page['items'][0]['outputDetail']
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.read_proven_field
    async def delayed(*values):
        result = await original(*values)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(owner, 'read_proven_field', delayed)
    task = asyncio.create_task(pages.read_text(session, args))
    await asyncio.wait_for(entered.wait(), 2)
    if change == 'client':
        monkeypatch.setattr(pages, '_client', lambda: 'other-browser')
    elif change == 'reopen':
        pages.read(session, 't@user')
    elif change == 'evict':
        pages.groups.clear()
    elif change == 'owner':
        service._session = lambda identity, hydrate=False: dict(session)
    elif change == 'source':
        session['nativeIdentity'] = 'replacement'
    elif change == 'epoch':
        owner.invalidate({'s'})
    elif change == 'position':
        session['execution']['nodes'].insert(0, {**session['execution']['nodes'][0], 'id': 'new'})
    elif change == 'group':
        session['execution']['nodes'][0]['anchorMessageId'] = 'other'
    elif change == 'phase':
        session['execution']['nodes'][0]['phase'] = 'running'
    elif change == 'revision':
        session['execution']['nodes'][0]['revision'] = 3
    elif change == 'proof':
        state['status'] = 'stale'
    else:
        bound['_eventFields']['output']['sha256'] = 'replaced'
    release.set()
    with pytest.raises(ValueError):
        await task
    assert len([call for call in calls if call[0] == 'field']) == 1


@pytest.mark.asyncio
async def test_same_stamp_reopen_expires_receipt_and_cursor(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch)
    first = owner.live_pages.read(session, 't@user')
    second = owner.live_pages.read(session, 't@user')
    assert first['items'][0]['outputDetail']['windowKey'] != second['items'][0]['outputDetail']['windowKey']
    with pytest.raises(ValueError):
        await owner.live_pages.read_text(session, first['items'][0]['outputDetail'])
    with pytest.raises(ValueError):
        owner.live_pages.read(session, 't@user', before=first['before'])


@pytest.mark.parametrize('change', ['client', 'reopen', 'evict', 'owner', 'epoch', 'position', 'body'])
@pytest.mark.asyncio
async def test_inline_field_runs_off_loop_and_revalidates_after_await(tmp_path, monkeypatch, change):
    import asyncio
    from amplifier_web import resident_work_pages
    owner, session, service = live_fixture(tmp_path, monkeypatch, count=2)
    pages = owner.live_pages
    args = pages.read(session, 't@user')['items'][0]['outputDetail']
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(function, *values, **kwargs):
        result = function(*values, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(resident_work_pages.asyncio, 'to_thread', delayed)
    task = asyncio.create_task(pages.read_text(session, args))
    await asyncio.wait_for(entered.wait(), 2)
    if change == 'client':
        monkeypatch.setattr(pages, '_client', lambda: 'other')
    elif change == 'reopen':
        pages.read(session, 't@user')
    elif change == 'evict':
        pages.groups.clear()
    elif change == 'owner':
        service._session = lambda identity, hydrate=False: dict(session)
    elif change == 'epoch':
        owner.invalidate({'s'})
    elif change == 'position':
        session['execution']['nodes'].reverse()
    else:
        session['execution']['nodes'][0]['output'] = 'edit ' * 1000
    release.set()
    with pytest.raises(ValueError):
        await task


@pytest.mark.asyncio
async def test_canonical_failure_never_falls_back_to_inline_field(tmp_path, monkeypatch):
    owner, session, _, _, _, calls, _ = proven_live_fixture(tmp_path, monkeypatch)
    session['execution']['nodes'][0]['output'] = 'inline fallback ' * 1000
    args = owner.live_pages.read(session, 't@user')['items'][0]['outputDetail']
    async def refused(*args):
        raise ValueError('canonical proof expired')
    monkeypatch.setattr(owner, 'read_proven_field', refused)
    with pytest.raises(ValueError, match='canonical proof expired'):
        await owner.live_pages.read_text(session, args)
    assert all(call[0] != 'field' for call in calls)


@pytest.mark.parametrize('field,value', [
    ('id', 'other'), ('digest', 'other'), ('detailVersion', 'other'),
    ('sessionId', 'other'), ('part', 'messages'),
])
@pytest.mark.asyncio
async def test_live_field_rejects_wrong_admission_before_reading(tmp_path, monkeypatch, field, value):
    owner, session, _, _, _, calls, _ = proven_live_fixture(tmp_path, monkeypatch)
    args = owner.live_pages.read(session, 't@user')['items'][0]['outputDetail']
    with pytest.raises(ValueError):
        await owner.live_pages.read_text(session, {**args, field: value})
    assert all(call[0] != 'field' for call in calls)


@pytest.mark.parametrize('canonical_duplicate', [False, True])
def test_admitted_position_never_comes_from_a_duplicate_or_filtered_row(tmp_path, monkeypatch, canonical_duplicate):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=2)
    session['execution']['nodes'][1]['id'] = '0'
    if canonical_duplicate:
        session['execution']['nodes'][1]['canonicalHistory'] = True
        result = owner.live_pages.read(session, 't@user')
        assert [row['id'] for row in result['items']] == ['0']
        assert owner.live_pages.groups[(None, 's', 't@user')]['positions'] == {'0': 0}
    else:
        with pytest.raises(ValueError):
            owner.live_pages.read(session, 't@user')
        assert not owner.live_pages.groups


@pytest.mark.parametrize('change', ['none', 'source', 'index', 'support', 'proof'])
@pytest.mark.parametrize('timing', ['before', 'during'])
@pytest.mark.asyncio
async def test_real_proof_live_refs_sync_and_fence_canonical_reads(canonical_source, monkeypatch, change, timing):
    """Actual emitter/index + in-process worker; no native/runtime/browser claim."""
    import asyncio
    import copy
    import json
    from amplifier_web import event_window
    from amplifier_web.browser_detail import page as detail_page
    from amplifier_web.work_windows import WorkWindows
    from test_work_windows import FakeProjector
    source = canonical_source
    source.call('one')
    await source.diagnostics.flush()
    session = source.session
    session['execution']['nodes'] = [copy.deepcopy(source.observer.nodes['tool:root:one'])]
    original = copy.deepcopy(session)
    owner = WorkWindows(source.service)
    owner.projector = FakeProjector(source.service.data_dir)
    pages = owner.live_pages
    group = detail_page(owner.live_view(session), 'groups')['items'][0]['id']
    task, release = None, asyncio.Event()
    try:
        before = pages.read(session, group)
        row = before['items'][0]
        assert 'inputDetail' not in row and 'outputDetail' not in row
        proof = await owner.prove('root')
        assert proof['status'] == 'complete'
        def forbidden(*args, **kwargs):
            raise AssertionError('Resident read/sync/field must not initiate a proof')
        monkeypatch.setattr(owner, 'prove', forbidden)
        monkeypatch.setattr(owner, 'request', forbidden)
        synced = pages.sync(session, group, {row['id']: row['detailVersion']}, paged=True)
        assert len(synced['items']) == 1
        row = synced['items'][0]
        assert row['detailVersion'] != before['items'][0]['detailVersion']
        assert 'inputDetail' in row and 'outputDetail' in row
        assert session == original
        args = {**row['outputDetail'], 'limit': 4096}
        entered, release = asyncio.Event(), asyncio.Event()
        project = owner.projector.project
        async def delayed(inputs, *, key):
            result = await project(inputs, key=key)
            if inputs['operation'] == 'field':
                entered.set()
                await release.wait()
            return result
        def invalidate():
            if change == 'source':
                path, _, _ = event_window._source(session)
                with path.open('ab') as handle:
                    handle.write(b'\n')
            elif change == 'index':
                with source.diagnostics._db() as db:
                    db.execute('DELETE FROM records')
            elif change == 'support':
                owner.pages.clear()
            elif change == 'proof':
                owner.invalidate({'root'})
        if timing == 'during':
            monkeypatch.setattr(owner.projector, 'project', delayed)
            task = asyncio.create_task(pages.read_text(session, args))
            await asyncio.wait_for(entered.wait(), 2)
            invalidate()
            release.set()
        else:
            invalidate()
            task = asyncio.create_task(pages.read_text(session, args))
        if change == 'none':
            result = await task
            pieces = [result['value']]
            for _ in range(64):
                if result['nextOffset'] is None:
                    break
                result = await pages.read_text(session, {**args, 'offset': result['nextOffset']})
                pieces.append(result['value'])
            else:
                pytest.fail('Bounded canonical read did not finish')
            assert json.loads(''.join(pieces)) == source.calls[0][3]
        else:
            with pytest.raises(ValueError):
                await task
            stale = pages.sync(session, group, {row['id']: row['detailVersion']}, paged=True)
            assert stale['items'][0]['detailVersion'] != row['detailVersion']
            assert 'outputDetail' not in stale['items'][0] and 'inputDetail' not in stale['items'][0]
        assert session == original
        assert not any('input' in value or 'output' in value for value in session['execution']['nodes'])
    finally:
        release.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.close()


@pytest.mark.parametrize('boundary', ['next-chunk', 'latest-finalization'])
def test_paged_revision_pins_older_admitted_rows_not_only_latest_tail(tmp_path, monkeypatch, boundary):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=144)
    first = owner.live_pages.read(session, 't@user')
    older = owner.live_pages.read(session, 't@user', before=first['before'])
    rows = older['items'] + first['items']
    a = owner.live_pages.sync(session, 't@user',
        {row['id']: row['detailVersion'] for row in rows[:100]}, paged=True)
    assert len(a['order']) == 100
    if boundary == 'latest-finalization':
        b = owner.live_pages.sync(session, 't@user',
            {row['id']: row['detailVersion'] for row in rows[100:]},
            paged=True, revision=a['revision'])
        assert b['revision'] == a['revision']
    session['execution']['nodes'][0]['output'] = 'Changed old body, unchanged latest100'
    with pytest.raises(ValueError, match='changed'):
        if boundary == 'next-chunk':
            owner.live_pages.sync(session, 't@user',
                {row['id']: row['detailVersion'] for row in rows[100:]},
                paged=True, revision=a['revision'])
        else:
            owner.live_pages.read(session, 't@user', preserve=True, revision=a['revision'])


def test_paged_sync_append_finalization_and_older_continuation_keep_valid_tokens(tmp_path, monkeypatch):
    owner, session, _ = live_fixture(tmp_path, monkeypatch, count=250)
    pages = owner.live_pages
    first = pages.read(session, 't@user')
    # Ordinary preserving opens accept the original projection revision too.
    preserved = pages.read(session, 't@user', preserve=True, revision=first['revision'])
    assert preserved['before'] == first['before']
    session['execution']['nodes'].append({**session['execution']['nodes'][-1], 'id': '250'})
    synced = pages.sync(session, 't@user',
        {row['id']: row['detailVersion'] for row in first['items']}, paged=True)
    latest = pages.read(session, 't@user', preserve=True, revision=synced['revision'])
    assert latest['revision'] == synced['revision']
    assert latest['items'][-1]['id'] == '250'
    middle = pages.read(session, 't@user', before=latest['before'], revision=latest['revision'])
    assert [row['id'] for row in middle['items']] == [str(i) for i in range(50, 150)]
    oldest = pages.read(session, 't@user', before=middle['before'], revision=middle['revision'])
    assert [row['id'] for row in oldest['items']] == [str(i) for i in range(50)]
    assert oldest['before'] is None
