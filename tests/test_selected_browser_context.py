"""Bundled streams carry selected context; the shell owns navigation pages."""
from copy import deepcopy

import pytest

from test_automatic_history import app_factory
from test_browser_state import catalog
from test_live_clients import command, snapshot
from test_history_directory_exact_ids import native_fixture, change_native


PAGES = {'chatNavigation', 'headerChatNavigation', 'workspaceExplorer', 'subagentNavigation'}


def setup(app_factory, *, roots=80, children=120):
    app = app_factory()
    rows = catalog(app, count=roots, workers=children, workspaces=4)
    for queue in list(app.queues):
        app.unsubscribe(queue)  # This fixture exercises attached web streams.
    for identity in ('web-a', 'web-b'):
        record = app.clients.attach(identity)
        record.update(selectedSessionId=rows[0]['id'], selectedWorkspaceId=rows[0]['workspaceId'])
        record['drafts'][rows[0]['id']] = f'Private {identity}'
        record['canvas'] = {'id': identity, 'kind': 'markdown', 'content': f'Canvas {identity}'}
    return app, rows


async def test_ordinary_web_publication_never_constructs_hidden_pages(app_factory, monkeypatch):
    app, rows = setup(app_factory)
    selected = app._session(rows[0]['id'], hydrate=False)
    selected.update(historyLoaded=True,
        messages=[{'id': 'answer', 'role': 'assistant', 'text': 'Selected response'}],
        workers=[{'id': 'visible-work', 'status': 'complete', 'label': 'Work retained'}],
        approvals=[{'id': 'permission', 'status': 'pending', 'tool': 'filesystem'}],
        questions=[{'id': 'question', 'status': 'pending', 'question': 'Proceed?'}])
    app._publish()
    expected = {key: deepcopy(selected[key]) for key in ('messages', 'approvals', 'questions')}
    counts = []
    original = app.directory.child_count
    monkeypatch.setattr(app.directory, 'child_count', lambda state, parent: (counts.append(parent['id']), original(state, parent))[1])
    for owner, name in ((app.projections, 'chats'), (app.projections, 'workspaces'),
                        (app.directory, 'children'), (app.directory, 'workspace_counts')):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail('A hidden navigation page was constructed'))
    queues = []
    for identity in ('web-a', 'web-b'):
        with app.clients.bind(identity):
            queues.append(app.subscribe())
    app._publish()
    for identity, queue in zip(('web-a', 'web-b'), queues):
        public = queue.get_nowait()
        assert public['browserResources'] == ['selectedContext']
        assert not PAGES.intersection(public)
        assert {row['id'] for row in public['sessions']} == {selected['id']}
        row = public['sessions'][0]
        assert {key: row[key] for key in expected} == expected
        assert row['subagentCount'] == 120
        assert any(worker['id'] == 'visible-work' for worker in row['workers'])
        assert row['draft'] == f'Private {identity}'
        assert public['canvas']['content'] == f'Canvas {identity}'
        assert public['attention']['sessions'][selected['id']] > 0
        assert public['library']['sessionCount'] == 80
    assert counts == [selected['id'], selected['id']]
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_child_page_is_private_scoped_and_retired_on_close(app_factory, monkeypatch):
    app, rows = setup(app_factory)
    selected = rows[0]['id']
    calls = []
    original = app.directory.children
    monkeypatch.setattr(app.directory, 'children', lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    requested = {'sessionId': selected, 'filter': '', 'index': 999}
    await command(app, 'web-a', 'view.update', {'patch': {'panel': 'subagent-history', 'subagentHistory': requested}})
    page = snapshot(app, 'web-a')['subagentNavigation']
    assert page['scope'] == requested
    assert (page['index'], page['start'], page['end'], page['total']) == (2, 100, 120, 120)
    assert snapshot(app, 'web-a')['browserResources'] == ['selectedContext', 'subagentNavigation']
    assert snapshot(app, 'web-b')['browserResources'] == ['selectedContext']
    assert 'subagentNavigation' not in snapshot(app, 'web-b')
    assert len(calls) == 1
    await command(app, 'web-a', 'view.update', {'patch': {'subagentHistory': {'sessionId': selected, 'filter': 'Conversation 19?', 'index': 0}}})
    page = snapshot(app, 'web-a')['subagentNavigation']
    assert page['scope'] == {'sessionId': selected, 'filter': 'Conversation 19?', 'index': 0}
    assert page['total'] == 10 and page['unfilteredTotal'] == 120
    # Fresh membership reaches a still-open page without a selection change.
    child = app._session(rows[-1]['id'], hydrate=False)
    child['parentId'] = rows[1]['id']; child['nativeParentId'] = rows[1]['nativeIdentity']
    child['historyManaged'] = False
    app._publish(session_ids={child['id']})
    assert snapshot(app, 'web-a')['subagentNavigation']['total'] == 9
    await command(app, 'web-a', 'view.update', {'patch': {'panel': None}})
    after_close = len(calls)
    public = snapshot(app, 'web-a')
    assert public['browserResources'] == ['selectedContext'] and not PAGES.intersection(public)
    assert {row['id'] for row in public['sessions']} == {selected}
    assert public['sessions'][0]['subagentCount'] == 119
    assert public['view']['draft'] == 'Private web-a'
    assert public['canvas']['content'] == 'Canvas web-a'
    app._publish()
    assert len(calls) == after_close
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_selected_child_keeps_exact_dormant_parent_and_voice_context(app_factory, monkeypatch):
    app, rows = setup(app_factory, roots=10, children=3)
    child = app._session(rows[-1]['id'], hydrate=False)
    voice = app._session(rows[1]['id'], hydrate=False)
    voice['messages'] = [{'id': 'voice-message', 'role': 'assistant', 'text': 'Voice conversation'}]
    child['messages'] = [{'id': 'child-message', 'role': 'assistant', 'text': 'Child conversation'}]
    child['historyLoaded'] = voice['historyLoaded'] = True
    app.state['sessions'] = [child, voice]
    app.state['selectedSessionId'] = child['id']
    app.state['voice']['sessionId'] = voice['id']
    record = app.clients.records['web-a']
    record.update(selectedSessionId=child['id'], selectedWorkspaceId=child['workspaceId'])
    app._publish()
    seen = []
    original = app.directory.summary_ids
    monkeypatch.setattr(app.directory, 'summary_ids', lambda ids, **kwargs: (seen.append(set(ids) - {None}), original(ids, **kwargs))[1])
    public = snapshot(app, 'web-a')
    projected = {row['id']: row for row in public['sessions']}
    assert set(projected) == {child['id'], voice['id'], rows[0]['id']}
    assert projected[child['id']]['parentId'] == rows[0]['id']
    assert projected[child['id']]['subagentCount'] == 0
    assert projected[rows[0]['id']]['title'] == rows[0]['title']
    assert projected[rows[0]['id']]['messages'] == []
    assert projected[voice['id']]['messages'][0]['text'] == 'Voice conversation'
    assert rows[0]['id'] not in {row['id'] for row in app.state['sessions']}
    assert seen == [{rows[0]['id']}]


async def test_explicit_and_independently_released_client_reads_keep_contract(app_factory):
    app, rows = setup(app_factory, roots=3, children=2)
    web = snapshot(app, 'web-a')
    assert not PAGES.intersection(web)
    with app.clients.bind('web-a'):
        full = app.get_state()
        detail = app.browser_state(session_id=rows[1]['id'])
        session = app.session_state(rows[1]['id'])
        custom = app.state_context()
    for public in (full, detail, custom):
        assert PAGES <= public.keys()
        assert 'browserResources' not in public
    assert len(full['sessions']) == 5
    assert [row['id'] for row in session['sessions']] == [rows[1]['id']]
    assert 'browserResources' not in session
    app.clients.attach('terminal', kind='tui')
    assert PAGES <= snapshot(app, 'terminal').keys()
    assert PAGES <= app.browser_state().keys()
    assert snapshot(app, 'web-a') is web


@pytest.mark.parametrize('count', [270, 27000])
def test_selected_child_count_is_indexed_and_never_decodes_a_page(tmp_path, monkeypatch, count):
    state, directory, catalog, project, workspace = native_fixture(tmp_path, count, 6)
    parent = state['sessions'][0]
    parent.update(nativeIdentity='saved-00000', nativeProject=project, historyManaged=True)
    directory.sync([parent], state); directory.db.commit()
    child_ids = [f'saved-{count-offset:05d}' for offset in range(1, 7)]
    changed = [{**catalog.get(project, identity), 'parentId': 'saved-00000', 'sessionKind': 'worker'} for identity in child_ids]
    change_native(catalog, project, workspace, changed)
    assert directory.children(state, parent)['unfilteredTotal'] == 6
    monkeypatch.setattr(directory, 'decode', lambda *a: pytest.fail('Count decoded a child body'))
    reader = directory.read_snapshot(state)
    vm = 0
    def progress():
        nonlocal vm
        vm += 1000
        return vm > 100000
    directory.db.set_progress_handler(progress, 1000)
    try:
        assert directory.child_count(state, parent) == 6
        assert directory.child_count(state, {'id': 'absent', 'workspace': '/absent'}) == 0
    finally:
        directory.db.set_progress_handler(None, 0)
    changed[0]['parentId'] = 'another-parent'
    change_native(catalog, project, workspace, [changed[0]])
    assert directory.child_count(state, parent) == 5
    try:
        assert reader.child_count(reader._frozen_state, parent) == 6
    finally:
        reader.close()
    assert len(state['sessions']) == 6
    print({'nativeRows': count, 'childCountVM': vm})


async def test_recovered_negative_page_index_has_current_request_coverage(app_factory):
    app, rows = setup(app_factory, roots=3, children=2)
    # Actions reject negative indices, but a recovered legacy view can be
    # malformed. Its echoed scope must agree with the bundled client normalizer.
    record = app.clients.records['web-a']
    record['view'].update(panel='subagent-history', subagentHistory={
        'sessionId': rows[0]['id'], 'filter': '', 'index': -1})
    page = snapshot(app, 'web-a')['subagentNavigation']
    assert page['scope']['index'] == page['index'] == 0
    assert page['total'] == 2
