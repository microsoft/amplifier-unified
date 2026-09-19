"""Large native libraries stay browseable without becoming per-click payloads."""
import asyncio
from copy import deepcopy
import json
import time
import uuid

import pytest

from amplifier_web import browser_state
from amplifier_web.service import AppService
from amplifier_web.session_files import project_slug
from test_automatic_history import app_factory, native_session, native_rows, finish_actions, files_snapshot


def catalog(app, count=5000, workers=120, workspaces=150):
    base = app.state['settings']['workspace']
    registrations = [{'id': f'workspace-{i}', 'path': f'{base}/project-{i}',
                      'name': f'project-{i}', 'available': True} for i in range(workspaces)]
    project = project_slug(base)
    rows = []
    for index in range(count + workers):
        native = f'conversation-{index}'
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project}/{native}').hex
        workspace = registrations[index % workspaces] if index < count else registrations[0]
        rows.append({'id': identity, 'title': f'Conversation {index}', 'titleSource': 'native',
            'description': 'Saved project conversation', 'status': 'idle', 'messages': [], 'workers': [], 'approvals': [],
            'nativeProject': project, 'nativeIdentity': native, 'runtimeSessionId': native,
            'workspace': workspace['path'], 'workspaceId': workspace['id'], 'workspaceAvailable': True,
            'historyManaged': True, 'historyLoaded': False,
            'sessionKind': 'root' if index < count else 'worker',
            'parentId': None if index < count else rows[0]['id'],
            'nativeParentId': None if index < count else rows[0]['nativeIdentity'],
            'recentActivityAt': 1000 + index, '_catalogRecentAt': 1000 + index,
            '_catalogId': identity, 'nativeRevision': [(1000 + index)*1_000_000_000, 120]})
    app.state.update(sessions=rows, workspaces=registrations, selectedSessionId=rows[0]['id'],
                     selectedWorkspaceId=registrations[0]['id'])
    app.state['view']['navChatScope'] = 'all'
    app._publish()
    return rows


async def test_large_catalog_bounded_receipts_publications_and_complete_agent_access(app_factory, monkeypatch):
    app = app_factory()
    rows = catalog(app)
    app.state['sessions'][0]['messages'] = [{'id':'selected-message','role':'assistant','text':'Selected chat retained'}]
    app.state['sessions'][1]['messages'] = [{'id':'other-message','role':'assistant','text':'NOT_IN_PUBLIC_ARCHIVE'*2000}]
    app._publish()
    queue = app.subscribe()
    builds = []
    original = browser_state.snapshot
    def observed(*args):
        builds.append(1)
        return original(*args)
    monkeypatch.setattr(browser_state, 'snapshot', observed)
    monkeypatch.setattr(app, 'get_state', lambda: pytest.fail('A browser action copied the complete catalog'))
    receipt = await app.dispatch('view.update', {'patch': {'panel':'settings'}})
    public = receipt['state']
    assert queue.get_nowait() is public and app.browser_state() is public
    assert len(builds) == 1
    assert len(public['sessions']) <= 250 and len(public['workspaces']) <= 250
    assert len(json.dumps(public)) < 1_000_000
    assert 'NOT_IN_PUBLIC_ARCHIVE' not in json.dumps(public)
    assert public['chatNavigation']['total'] == 5000
    assert public['subagentNavigation']['total'] == 120
    assert next(row for row in public['sessions'] if row['id'] == rows[0]['id'])['messages'][0]['text'] == 'Selected chat retained'
    saved = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert len(saved['sessions']) == 1
    page = await app.app_bridge('get_state', {'path':'/sessions','offset':4500,'limit':3}, rows[0]['id'])
    assert page['total'] == 5120 and page['items'][0]['value']['id'] == rows[4500]['id']
    result = await app.app_bridge('dispatch', {'action':'session.pin','args':{'id':rows[4500]['id'],'pinned':True}}, rows[0]['id'])
    assert (await app.app_bridge('get_state', {'path':'/chatNavigation/items/0/id'}, rows[0]['id']))['value'] == rows[4500]['id']
    pointed = await app.app_bridge('get_state', {'path': result['state']['session']['$statePath']}, rows[0]['id'])
    assert next(row['value'] for row in pointed['items'] if row['key']=='id') == rows[0]['id']
    app.unsubscribe(queue)


async def test_navigation_pages_keep_worker_parent_and_full_workspace_counts(app_factory):
    app = app_factory(); rows = catalog(app, count=5000, workers=120, workspaces=150)
    first = app.browser_state()
    assert first['workspaceExplorer']['totalWorkspaces'] == 150
    assert first['headerChatNavigation']['total'] == 34
    assert next(row for row in first['sessions'] if row['id']==rows[0]['id'])['subagentCount'] == 120
    result = await app.dispatch('view.update', {'patch': {'subagentHistory':{'sessionId': rows[0]['id'], 'filter':'', 'index':2}}})
    page = result['state']['subagentNavigation']
    assert (page['index'], page['start'], page['end'], page['total'], page['unfilteredTotal']) == (2,100,120,120,120)
    await app.dispatch('view.update', {'patch': {'subagentHistory':{'sessionId': rows[0]['id'], 'filter':'Conversation 511?', 'index':0}}})
    assert app.browser_state()['subagentNavigation']['total'] == 10
    await app.dispatch('view.update', {'patch': {'subagentHistory':{'sessionId': rows[0]['id'], 'filter':'conversation-511?', 'index':0}}})
    assert app.browser_state()['subagentNavigation']['total'] == 10
    # A direct off-page agent selection must retain its origin navigation.
    app.state['selectedSessionId'] = rows[-1]['id']; app._publish()
    assert rows[0]['id'] in {row['id'] for row in app.browser_state()['sessions']}
    # Worker aliases can still be found before discovery remaps parentId.
    child = deepcopy(rows[-1]);child.pop('parentId')
    assert browser_state.direct_child(child, rows[0])
    child['nativeProject'] = 'another-project'
    assert not browser_state.direct_child(child, rows[0])


async def test_offpage_error_and_approval_still_produce_attention(app_factory):
    app = app_factory(); rows = catalog(app)
    rows[4500]['error'] = 'Provider unavailable'
    rows[4501]['approvals'] = [{'id':'pending-permission', 'status':'pending', 'tool':'filesystem'}]
    app._publish()
    attention = {row['id']: row for row in app.browser_state()['attention']['items']}
    failed = attention['session:'+rows[4500]['id']]
    approval = attention['approval:pending-permission']
    assert (failed['sessionId'], failed['workspace']) == (rows[4500]['id'], rows[4500]['workspace'])
    assert (approval['sessionId'], approval['workspace']) == (rows[4501]['id'], rows[4501]['workspace'])
    assert app.browser_state()['attention']['sessions'][rows[4500]['id']] == 1
    assert app.browser_state()['attention']['sessions'][rows[4501]['id']] == 1
    # Actions resolve the full catalog, even if a row has no browser summary.
    await app.dispatch('session.rename', {'id':rows[4500]['id'],'title':'Renamed off page'})
    assert app._session(rows[4500]['id'])['title'] == 'Renamed off page'


async def test_native_overrides_survive_restart_without_persisting_the_catalog(tmp_path, app_factory):
    directories = [native_session(tmp_path/'native', f'root-{index}') for index in range(8)]
    app = app_factory(); await app.history.refresh()
    rows = native_rows(app); selected, pinned, manual, draft, active = rows[:5]
    await app.dispatch('session.select', {'id':selected['id']}); await finish_actions(app)
    await app.dispatch('session.pin', {'id':pinned['id'],'pinned':True})
    await app.dispatch('session.rename', {'id':manual['id'],'title':'My saved label'})
    await app.dispatch('attachment.add', {'sessionId':draft['id'],'name':'notes.txt','base64':'aGVsbG8='})
    app._session(active['id'])['recentActivityAt'] = time.time() + 100
    app._publish()
    before = [files_snapshot(directory) for directory in directories]
    saved = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert len(saved['sessions']) == 5
    home, workspace = app.data_dir, app.default_workspace
    await app.close(); restored = app_factory(home=home, workspace=__import__('pathlib').Path(workspace))
    assert restored.state['selectedSessionId'] == selected['id']
    assert restored.state['pinnedSessionIds'] == [pinned['id']]
    assert restored._session(manual['id'])['title'] == 'My saved label'
    assert restored._session(draft['id'])['draftAttachments'][0]['name'] == 'notes.txt'
    assert restored._session(active['id'])['recentActivityAt'] > time.time()
    await restored.history.refresh()
    assert len(native_rows(restored)) == 8
    assert restored._session(selected['id'])['messages']
    assert restored._session(manual['id'])['title'] == 'My saved label'
    assert [files_snapshot(directory) for directory in directories] == before


async def test_stream_publications_coalesce_but_final_response_flushes(app_factory):
    app = app_factory(); await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']; queue = app.subscribe(); revision = app.state['revision']
    for _ in range(40):
        await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'x'})
    assert queue.empty() and app.state['revision'] == revision
    await app.on_runtime_event('assistant.message', {'sessionId':sid,'text':'Final response'})
    final = queue.get_nowait()
    assert final['sessions'][0]['messages'][-1]['text'] == 'Final response'
    assert app.state['revision'] == revision + 1
    await asyncio.sleep(.3)
    assert queue.empty()
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'last chunk'})
    await asyncio.sleep(.3)
    assert queue.get_nowait()['sessions'][0]['streaming'] == 'last chunk'
    await app.on_runtime_event('assistant.delta', {'sessionId':sid,'text':' before close'})
    await app.close()
    saved = AppService(app.data_dir, workspace=app.default_workspace)
    assert saved._session(sid)['streaming'] == 'last chunk before close'
    await saved.close()


async def test_offpage_text_notifications_keep_compact_final_messages(app_factory):
    app = app_factory(); rows = catalog(app)
    target = rows[4501]
    target['messages'] = [{'id':'text-response','role':'assistant','via':'text','text':'A useful result'*10000,'createdAt':100}]
    target['status'] = 'idle'; app._publish()
    public = app.browser_state()
    assert public['notificationMessages'] == [{'id':'text-response','role':'assistant','via':'text','sessionId':target['id'],'createdAt':100,'text':('A useful result'*10000)[:500]}]
    assert target['id'] not in {row['id'] for row in public['sessions']}
    app.state['notificationSettings'] = {'preview':False}; app._publish()
    assert 'text' not in app.browser_state()['notificationMessages'][0]


async def test_voice_session_and_direct_saved_changes_keep_current_snapshot(app_factory):
    app = app_factory(); rows = catalog(app)
    target = rows[4501]
    target['messages'] = [{'id':'voice-result','role':'assistant','via':'call','text':'Voice result','createdAt':100}]
    app.state['voice'] = {'sessionId':target['id'], 'status':'connected'}
    app._publish()
    assert next(row for row in app.browser_state()['sessions'] if row['id']==target['id'])['messages'][0]['text'] == 'Voice result'
    app.state['updates'] = {'phase':'checking', 'detail':'Direct saved progress'}
    app._save()
    assert app.browser_state()['updates']['detail'] == 'Direct saved progress'
    for invalid in ({'sessionId':[]}, {'index':True}, {'index':-1}, {'filter':None}):
        with pytest.raises(Exception, match='history|conversation'):
            await app.dispatch('view.update', {'patch':{'subagentHistory':invalid}})


async def test_explicit_snapshot_target_does_not_change_selection_or_default_cache(app_factory):
    app = app_factory(); rows = catalog(app)
    target = rows[4501]
    target['messages'] = [{'id':'finished','role':'assistant','text':'Headless result','createdAt':100}]
    target['generations'] = [{'event':'generation.finished','text':'Headless result','generation_id':'done'}]
    app._publish()
    selected = app.state['selectedSessionId']
    original = app.browser_state()
    assert target['id'] not in {row['id'] for row in original['sessions']}
    scoped = app.browser_state(session_id=target['id'])
    assert scoped['selectedSessionId'] == selected == app.state['selectedSessionId']
    assert next(row for row in scoped['sessions'] if row['id'] == target['id'])['generations'][0]['generation_id'] == 'done'
    assert app.browser_state() is original
    assert target['id'] not in {row['id'] for row in app.browser_state()['sessions']}
