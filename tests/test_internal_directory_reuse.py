"""Internal work reuse is durable membership, not resident-cache membership."""
import pytest

from amplifier_web.service import AppService
from test_service import Runtime


@pytest.mark.parametrize('boundary', ['retire', 'restart'])
async def test_internal_create_reuses_dormant_owner_without_admitting_other_candidates(tmp_path, monkeypatch, boundary):
    app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title':'Human chat'})
        selected = app.state['selectedSessionId']
        # More than one lookup page of wrong-bundle matches must remain cold.
        for index in range(18):
            app.state['sessions'].append(app._new_session({'purpose':'terminal-tool', 'bundle':'other', 'title':str(index)}))
        app._save()
        result = await app.dispatch('session.create', {'purpose':'terminal-tool', 'bundle':'work'})
        reused = result['result']['sessionId']
        app._save()
        if boundary == 'restart':
            await app.close()
            app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
        else:
            # Exercise actual retirement after browsing more than the recent
            # working-set window, without changing the human chat selection.
            others = [row['id'] for row in app.directory.select(
                "session_kind='internal' AND id!=?", (reused,), limit=32)]
            for identity in others:
                app._session(identity, hydrate=False)
                app.cold_display.touch(identity)
                app._publish()
        assert reused not in {row['id'] for row in app.state['sessions']}
        before = {row['id'] for row in app.state['sessions']}
        original = app._admit_session
        admissions = []
        def chosen_only(metadata):
            if metadata['id'] not in before:
                assert metadata['id'] == reused
                admissions.append(metadata['id'])
            return original(metadata)
        monkeypatch.setattr(app, '_admit_session', chosen_only)
        second = await app.dispatch('session.create', {'purpose':'terminal-tool', 'bundle':'work'})
        assert second['result']['sessionId'] == reused
        assert admissions == [reused]
        assert app.state['selectedSessionId'] == selected
        assert not app.runtime.sent
    finally:
        await app.close()


@pytest.mark.parametrize('patch', [
    {'sessionPurpose':'different'}, {'bundle':'different'}, {'status':'working'},
    {'configurationBusy':True}, {'approvals':[{'id':'approval','status':'pending'}]},
])
async def test_internal_reuse_keeps_existing_eligibility_guards(tmp_path, patch):
    app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    try:
        row = app._new_session({'purpose':'terminal-tool', 'bundle':'work'})
        identity = row['id']
        row.update(patch)
        app.state['sessions'].append(row)
        app._save()
        result = await app.dispatch('session.create', {'purpose':'terminal-tool', 'bundle':'work'})
        assert result['result']['sessionId'] != identity
        assert not app.runtime.sent
    finally:
        await app.close()
