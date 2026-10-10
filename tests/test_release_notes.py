import copy
import json

import pytest

from amplifier_web import __version__, app_updates, release_notes
from amplifier_web.attention import snapshot
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager
from test_service import Runtime


def entry(number='99.0.0'):
    return {'version':number,'title':'Configuration changes','changes':['Updated defaults.'],
            'notices':[{'id':'defaults','title':'Review defaults','detail':'Defaults are now shared.',
                        'action':'Check your workspace settings.'}]}


def document(entries):
    return json.dumps({'schemaVersion':1,'releases':entries})


async def test_fresh_install_notices_start_at_installed_version_and_survive_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_web.__version__', '2.0.0')
    monkeypatch.setattr(release_notes, 'bundled', lambda: [entry('2.0.0'), entry('1.0.0')])
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    UpdateManager(app)
    assert [item['releaseVersion'] for item in snapshot(app.state)['items']] == ['2.0.0']
    assert snapshot(app.state)['unread'] == 1  # Current guidance still needs attention.
    assert len(app.state['updates']['application']['releaseNotes']) == 2
    await app.close()

    monkeypatch.setattr('amplifier_web.__version__', '4.0.0')
    monkeypatch.setattr(release_notes, 'bundled', lambda: [entry(f'{i}.0.0') for i in range(4, 0, -1)])
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    UpdateManager(restored)
    assert restored.state['releaseNoticeStartVersion'] == '2.0.0'
    assert [item['releaseVersion'] for item in snapshot(restored.state)['items']] == ['4.0.0', '3.0.0', '2.0.0']
    assert len(restored.state['updates']['application']['releaseNotes']) == 4
    await restored.close()


async def test_legacy_install_keeps_unread_guidance_even_without_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, 'bundled', lambda: [entry(__version__), entry('0.1.0')])
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    app.state.pop('releaseNoticeStartVersion')  # Saved state from before this field existed.
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    UpdateManager(restored)
    assert 'releaseNoticeStartVersion' not in restored.state
    assert snapshot(restored.state)['unread'] == 2
    await restored.close()


def test_packaged_history_has_current_release_and_retains_initial_changes():
    notes=release_notes.parse(release_notes.PATH.read_text(),__version__)
    assert notes[0]['version']==__version__
    assert {'0.11.0','0.11.1'} <= {row['version'] for row in notes}


@pytest.mark.parametrize('value',[None,{},[],{'schemaVersion':2,'releases':[]},
    {'schemaVersion':1,'releases':[None]}, {'schemaVersion':1,'releases':[entry(),entry()]},
    {'schemaVersion':1,'releases':[{**entry(),'version':'main'}]},
    {'schemaVersion':1,'releases':[{**entry(),'changes':'oops'}]},
    {'schemaVersion':1,'releases':[{**entry(),'notices':[{'id':'bad/url'}]}]}])
def test_invalid_history_rejected(value):
    with pytest.raises(ValueError):release_notes.parse(json.dumps(value))


def test_history_limits_and_exact_published_version():
    with pytest.raises(ValueError):release_notes.parse(' '*256001)
    with pytest.raises(ValueError):release_notes.parse(document([entry()]),'98.0.0')
    with pytest.raises(ValueError):release_notes.parse(document([entry('98.0.0')]),'99.0.0')
    assert release_notes.parse(document([entry('98.0.0'),entry()]),'99.0.0')[0]['version']=='99.0.0'


@pytest.mark.parametrize('count', [101, release_notes.MAX_RELEASES])
def test_history_retains_more_than_one_hundred_releases_within_bounds(monkeypatch, count):
    notes = release_notes.parse(document([entry(f'0.1.{i}') for i in range(count)]))
    monkeypatch.setattr(release_notes, 'bundled', lambda: notes)
    retained = release_notes.history()
    assert len(retained) == count
    assert retained[0]['version'] == f'0.1.{count - 1}'
    assert retained[-1]['version'] == '0.1.0'


def test_release_count_remains_bounded():
    raw = document([entry(f'0.1.{i}') for i in range(release_notes.MAX_RELEASES + 1)])
    assert len(raw.encode('utf-8')) < release_notes.MAX_BYTES
    with pytest.raises(ValueError, match='bounded release history'):
        release_notes.parse(raw)


@pytest.mark.parametrize('previous',[None,'Old title',['Old title']*6,[''],[None],['x'*161]])
def test_invalid_previous_notice_titles_rejected(previous):
    release=entry()
    release['notices'][0]['previousTitles']=previous
    with pytest.raises(ValueError):release_notes.parse(document([release]))


async def test_exact_release_history_includes_skipped_versions_and_failure_does_not_block_update(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(app_updates.components,'updates',AsyncMock(return_value=[]))
    monkeypatch.setattr(app_updates.shutil,'which',lambda _:'/tool')
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        if args[0]=='git':return 'a'*40+'\trefs/tags/v99.0.0'
        if args[-1].endswith('/latest'):return json.dumps({'tag_name':'v99.0.0'})
        assert args[-1].endswith('release-notes.json?ref='+'a'*40)
        return document([entry(),entry('98.0.0')])
    monkeypatch.setattr(app_updates,'process',process)
    checked=await app_updates.check()
    assert checked['status']=='update'
    assert [row['version'] for row in checked['releaseNotes']][:2]==['99.0.0','98.0.0']
    assert len(calls)==3
    original=process
    async def missing(*args,**kwargs):
        if 'contents/' in args[-1]:raise RuntimeError('private credentials must not leak')
        return await original(*args,**kwargs)
    monkeypatch.setattr(app_updates,'process',missing)
    checked=await app_updates.check()
    assert checked['status']=='update' and checked['releaseNotesWarning']
    assert checked['releaseNotes'][0]['version']==__version__
    assert 'private credentials must not leak' not in json.dumps(checked)


async def test_notice_review_survives_restart_and_upgrade_but_changed_guidance_is_unread(tmp_path,monkeypatch):
    monkeypatch.setattr(release_notes,'bundled',lambda:[])
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(app)
    app.state['updates']['application'].update(latest='v99.0.0',releaseNotes=[entry()])
    item=snapshot(app.state)['items'][0]
    assert item['page']=='updates' and not item['read']
    await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
    assert snapshot(app.state)['unread']==0
    await app.close()
    monkeypatch.setattr('amplifier_web.__version__','99.0.0')
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    UpdateManager(restored)
    assert snapshot(restored.state)['items'][0]['read']
    assert restored.state['updates']['application']['releaseNotes'][0]['notices']
    restored.state['updates']['application']['releaseNotes'][0]['notices'][0]['action']='Review the changed guidance.'
    changed=snapshot(restored.state)['items'][0]
    assert not changed['read'] and changed['fingerprint']!=item['fingerprint']
    await restored.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
    assert not snapshot(restored.state)['items'][0]['read']
    await restored.close()


@pytest.mark.parametrize('changed_field',['detail','action','title'])
async def test_editorial_notice_title_preserves_review_after_upgrade_but_changed_content_is_unread(tmp_path,monkeypatch,changed_field):
    original=entry(__version__)
    monkeypatch.setattr(release_notes,'bundled',lambda:[original])
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    UpdateManager(app)
    reviewed=snapshot(app.state)['items'][0]
    await app.dispatch('attention.read',{'ids':[reviewed['id']],
        'fingerprints':{reviewed['id']:reviewed['fingerprint']}})
    await app.close()

    corrected=copy.deepcopy(original)
    notice=corrected['notices'][0]
    notice['previousTitles']=[notice['title']]
    notice['title']='Shared workspace defaults'
    monkeypatch.setattr(release_notes,'bundled',lambda:release_notes.parse(document([corrected])))
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    UpdateManager(restored)
    current=snapshot(restored.state)['items'][0]
    assert current['title']=='Shared workspace defaults'
    assert current['fingerprint']!=reviewed['fingerprint'] and current['read']

    changed=restored.state['updates']['application']['releaseNotes'][0]['notices'][0]
    changed[changed_field]='Changed content.'
    if changed_field=='title':changed.pop('previousTitles')
    assert not snapshot(restored.state)['items'][0]['read']
    await restored.close()


def test_installed_history_wins_and_cached_upcoming_history_survives_without_latest(monkeypatch):
    installed=entry(__version__)
    monkeypatch.setattr(release_notes,'bundled',lambda:[installed])
    stale=copy.deepcopy(installed);stale['title']='Old wording'
    notes=release_notes.history([stale,entry()])
    assert notes[0]['version']=='99.0.0'
    assert next(row for row in notes if row['version']==__version__)['title']==installed['title']
    assert len(release_notes.history([{'invalid':True}]))==1


@pytest.mark.parametrize('failure',['check','notes'])
async def test_failed_refresh_preserves_saved_upcoming_notices_across_restart(tmp_path,monkeypatch,failure):
    from unittest.mock import AsyncMock
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(app)
    app.state['updates']['application'].update(latest='v99.0.0',revision='a'*40,releaseNotes=[entry()])
    result={**app_updates.application_state(),'status':'check_failed'} if failure=='check' else {
        **app_updates.application_state(),'status':'update','latest':'v99.0.0','revision':'a'*40,'releaseNotesWarning':'Unavailable'}
    monkeypatch.setattr(app_updates,'check',AsyncMock(return_value=result))
    monkeypatch.setattr(manager,'inventory_sources',AsyncMock(return_value=[]))
    await manager.check()
    assert app.state['updates']['application']['releaseNotes'][0]['version']=='99.0.0'
    await app.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    UpdateManager(restored)
    assert restored.state['updates']['application']['releaseNotes'][0]['version']=='99.0.0'
    await restored.close()
