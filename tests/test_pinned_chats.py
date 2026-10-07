from copy import deepcopy
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.state_records import load


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', workspace=tmp_path)
    await service.dispatch('session.create', {'title':'First'})
    service.clients.attach('one'); service.clients.attach('two')
    yield service
    await service.close()


async def test_pins_commit_without_session_saves_and_keep_pending_work(app, monkeypatch):
    sid = app._state['sessions'][0]['id']
    before = deepcopy(app._state['sessions'])
    with app.clients.bind('two'):
        other = app.subscribe()
    app._publish_progress(session_ids={sid}, detail_only=True, record_only=True)
    pending = app._progress_publish_task
    def forbidden(*args, **kwargs):
        pytest.fail('Pinning must not hydrate or save session data')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr(app.cold_display, 'hydrate', forbidden)
        with app.clients.bind('one'):
            result = await app.dispatch('session.pin', {'id':sid,'pinned':True}, command_id='pin', include_state=False)
    assert result['accepted']
    assert app._state['sessions'] == before
    assert app._progress_dirty and app._progress_publish_task is pending
    assert load(app.db)['pinnedSessionIds'] == [sid]
    assert other.get_nowait()['pinnedSessionIds'] == [sid]
    await app.dispatch('session.pin', {'id':sid,'pinned':False}, include_state=False)
    with app.clients.bind('one'):
        duplicate = await app.dispatch('session.pin', {'id':sid,'pinned':True}, command_id='pin', include_state=False)
    assert duplicate['duplicate']
    assert load(app.db)['pinnedSessionIds'] == []
    app.unsubscribe(other)


async def test_pin_failure_rolls_back_receipt_and_preference(app, monkeypatch):
    sid = app._state['sessions'][0]['id']
    before = deepcopy(app._state)
    def fail(*args, **kwargs): raise OSError('fixture disk full')
    with monkeypatch.context() as patch:
        patch.setattr(app.clients, 'save', fail)
        with pytest.raises(OSError):
            await app.dispatch('session.pin', {'id':sid,'pinned':True}, command_id='failed')
    assert app._state == before
    assert app.db.execute("SELECT receipt FROM commands WHERE id='failed'").fetchone() is None
    assert (await app.dispatch('session.pin', {'id':sid,'pinned':True}, command_id='failed'))['accepted']


async def test_cannot_pin_child(app):
    row = app._state['sessions'][0]
    row['sessionKind'] = 'worker'
    row['nativeParentId'] = 'parent'
    app.projections.invalidate()
    with pytest.raises(AppError, match='top-level'):
        await app.dispatch('session.pin', {'id':row['id'],'pinned':True})


async def test_warm_pin_reuses_facts_and_keeps_old_browser_snapshot_immutable(app, monkeypatch):
    from amplifier_web import chat_navigation, workspace_navigation
    from amplifier_web.browser_state import snapshot
    sid=app._state['sessions'][0]['id']
    with app.clients.bind('one'):
        before=app.browser_state()
        old=deepcopy(before)
        def forbidden(*args,**kwargs):pytest.fail('A pin must not rebuild library facts')
        with monkeypatch.context() as patch:
            patch.setattr(chat_navigation,'catalog',forbidden)
            patch.setattr(workspace_navigation,'_index',forbidden)
            result=await app.dispatch('session.pin',{'id':sid,'pinned':True})
        assert result['state']['pinnedSessionIds']==[sid]
        assert before==old
        optimized=deepcopy(app.projections.browser(app.state))
        app.projections.invalidate()
        assert app.projections.browser(app.state)==optimized
