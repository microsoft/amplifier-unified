import copy
import json

import pytest

from amplifier_web import __version__, app_updates, release_notes
from amplifier_web.attention import snapshot
from amplifier_web.updates import UpdateManager


@pytest.fixture
def service_types():
    # Metadata-only checks do not need the app service's optional dependencies.
    from amplifier_web.service import AppService
    from test_service import Runtime
    return AppService, Runtime


def entry(number='99.0.0'):
    return {'version':number,'title':'Configuration changes','changes':['Updated defaults.'],
            'notices':[{'id':'defaults','title':'Review defaults','detail':'Defaults are now shared.',
                        'action':'Check your workspace settings.'}]}


def document(entries):
    return json.dumps({'schemaVersion':1,'releases':entries})


async def test_fresh_install_notices_start_at_installed_version_and_survive_upgrade(tmp_path, monkeypatch, service_types):
    AppService, Runtime = service_types
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


async def test_legacy_install_keeps_unread_guidance_even_without_chats(tmp_path, monkeypatch, service_types):
    AppService, Runtime = service_types
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


@pytest.mark.parametrize('day', ['2026-11-17', '2024-02-29', '2000-02-29'])
def test_optional_calendar_date_survives_validation_and_retention(day):
    dated = {**entry(), 'date': day}
    assert release_notes.parse(document([dated])) == [dated]
    assert release_notes.retain([dated]) == [dated]
    assert 'date' not in release_notes.parse(document([entry()]))[0]


@pytest.mark.parametrize('day', [None, '', 20261117, True, [], {}, '2026-2-01',
    '20260201', '2026-11-17T00:00:00Z', ' 2026-11-17', '2026-11-17\n',
    '2026-02-29', '1900-02-29', '2026-04-31', '2026-13-01', '0000-01-01'])
def test_invalid_release_dates_are_rejected(day):
    with pytest.raises(ValueError):
        release_notes.parse(document([{**entry(), 'date': day}]))


@pytest.mark.parametrize('installed_date', [None, '2026-11-17'])
def test_history_preserves_whole_record_date_precedence_and_future_cache(monkeypatch, installed_date):
    installed = entry(__version__)
    if installed_date:
        installed['date'] = installed_date
    stale = {**installed, 'date': '2026-11-16', 'title': 'Stale'}
    upcoming = {**entry(), 'date': '2026-11-18'}
    monkeypatch.setattr(release_notes, 'bundled', lambda: [installed])
    assert release_notes.history([stale, upcoming]) == [upcoming, installed]
    assert release_notes.history([stale, upcoming], latest='v' + __version__) == [installed]


def test_history_limits_and_exact_published_version():
    with pytest.raises(ValueError, match='too large'):
        release_notes.parse(' ' * (release_notes.MAX_BYTES + 1))
    with pytest.raises(ValueError):release_notes.parse(document([entry()]),'98.0.0')
    with pytest.raises(ValueError):release_notes.parse(document([entry('98.0.0')]),'99.0.0')
    assert release_notes.parse(document([entry('98.0.0'),entry()]),'99.0.0')[0]['version']=='99.0.0'


def test_valid_near_byte_limit_cached_unicode_history_survives(monkeypatch):
    notes = [dict(entry(f'99.0.{i}'), changes=['é' * 1400] * 12) for i in range(15)]
    compact = json.dumps({'schemaVersion': 1, 'releases': notes},
                         ensure_ascii=False, separators=(',', ':'))
    assert release_notes.MAX_BYTES * .95 < len(compact.encode('utf-8')) <= release_notes.MAX_BYTES
    assert len(document(notes).encode('utf-8')) > release_notes.MAX_BYTES
    parsed = release_notes.parse(compact)
    monkeypatch.setattr(release_notes, 'bundled', lambda: [])
    assert release_notes.history(parsed) == parsed
    # The bound applies to UTF-8 bytes, not Python's character count.
    with pytest.raises(ValueError, match='too large'):
        release_notes.parse(compact + ' ' * (release_notes.MAX_BYTES - len(compact.encode('utf-8'))) + 'é')


@pytest.mark.parametrize('count', [101, 257, release_notes.MAX_RELEASES])
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
    async def metadata(path, **kwargs):
        return await app_updates.process('gh', 'api', path)
    monkeypatch.setattr(app_updates, 'github_read', metadata)
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


async def test_notice_review_survives_restart_and_upgrade_but_changed_guidance_is_unread(tmp_path,monkeypatch,service_types):
    AppService, Runtime = service_types
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
async def test_editorial_notice_title_preserves_review_after_upgrade_but_changed_content_is_unread(tmp_path,monkeypatch,changed_field,service_types):
    AppService, Runtime = service_types
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


def ordinary(number):
    return {**entry(number), 'notices': []}


def test_retention_keeps_all_notices_and_latest_ordinary_without_mutating_inputs():
    protected = entry('0.0.1')
    protected['notices'][0]['previousTitles'] = ['Original title']
    protected['authoredExtension'] = {'keep': ['exact']}
    rows = [ordinary(f'0.1.{i}') for i in range(300)] + [protected]
    before = copy.deepcopy(rows)
    kept = release_notes.retain(rows)
    assert kept == [*reversed(rows[44:300]), protected]
    assert rows == before
    kept[-1]['notices'][0]['action'] = 'Detached'
    assert rows == before
    assert release_notes.MAX_RELEASES == 512 and release_notes.MAX_BYTES == 512_000


def test_retention_reclaims_ordinary_count_capacity_for_old_notices():
    protected = [entry(f'0.0.{i}') for i in range(300)]
    rows = [ordinary(f'0.1.{i}') for i in range(300)] + protected
    kept = release_notes.retain(rows)
    assert len(kept) == 512
    assert [r for r in kept if r['notices']] == list(reversed(protected))
    assert [r['version'] for r in kept if not r['notices']] == [
        f'0.1.{i}' for i in range(299, 87, -1)]


def test_retention_uses_utf8_bytes_and_removes_oldest_ordinary_first(monkeypatch):
    protected = entry('0.0.1')
    first, second = ordinary('0.1.1'), ordinary('0.1.2')
    first['changes'] = second['changes'] = ['é' * 1000]
    limit = len(release_notes.serialize([second, protected]).encode('utf-8'))
    monkeypatch.setattr(release_notes, 'MAX_BYTES', limit)
    kept = release_notes.retain([first, protected, second])
    assert kept == [second, protected]
    assert len(release_notes.serialize(kept).encode('utf-8')) == limit


@pytest.mark.parametrize('pressure', ['count', 'bytes'])
def test_protected_only_overflow_is_explicit_not_an_empty_history(monkeypatch, pressure):
    rows = [entry('0.1.0'), entry('0.1.1')]
    monkeypatch.setattr(release_notes, 'MAX_RELEASES' if pressure == 'count' else 'MAX_BYTES', 1)
    with pytest.raises(release_notes.HistoryOverflow, match='protected'):
        release_notes.retain(rows)


def test_valid_notice_text_can_exceed_real_utf8_budget_without_losing_guidance():
    rows = [dict(entry(f'0.1.{i}'), changes=['é' * 1400] * 12) for i in range(16)]
    assert len(release_notes.serialize(rows).encode('utf-8')) > release_notes.MAX_BYTES
    assert len(rows) < release_notes.MAX_RELEASES
    with pytest.raises(release_notes.HistoryOverflow, match='protected'):
        release_notes.retain(rows)


def test_required_prepared_version_must_fit_beside_protected_notices(monkeypatch):
    monkeypatch.setattr(release_notes, 'MAX_RELEASES', 1)
    with pytest.raises(release_notes.HistoryOverflow, match='current release'):
        release_notes.retain([entry('0.1.0'), ordinary('0.1.1')], expected='0.1.1')


@pytest.mark.parametrize('invalid', [
    {'version': '0.0.0', 'title': 'Bad', 'changes': [], 'notices': []},
    ordinary('0.1.299'),
    ordinary('99.0.0'),
])
def test_retention_validates_even_rows_that_would_be_discarded(invalid):
    rows = [ordinary(f'0.1.{i}') for i in range(300)] + [invalid]
    with pytest.raises(ValueError):
        release_notes.retain(rows, expected='0.1.299')


def test_cached_ordinary_cannot_displace_packaged_notices_or_aliases(monkeypatch):
    protected = entry('0.0.1')
    protected['notices'][0]['previousTitles'] = ['Earlier title']
    stale = ordinary('0.0.1')
    cached = [ordinary(f'0.1.{i}') for i in range(511)] + [stale]
    before = copy.deepcopy(cached)
    monkeypatch.setattr(release_notes, 'bundled', lambda: [protected])
    kept = release_notes.history(cached)
    assert kept == [*[ordinary(f'0.1.{i}') for i in range(510, 254, -1)], protected]
    assert cached == before
    assert release_notes.notice_id(kept[-1], kept[-1]['notices'][0]) == 'release-notice:0.0.1:defaults'


def test_cached_conflict_does_not_revive_superseded_packaged_guidance(monkeypatch):
    monkeypatch.setattr(release_notes, 'bundled', lambda: [ordinary('0.0.1')])
    assert release_notes.history([entry('0.0.1')]) == [ordinary('0.0.1')]
    assert release_notes.history([entry('0.0.2'), entry('0.0.2')]) == [ordinary('0.0.1')]


def test_packaged_precedence_is_resolved_before_any_source_is_pruned(tmp_path, monkeypatch):
    rows = [ordinary('0.0.1'), *[ordinary(f'0.1.{i}') for i in range(300)]]
    path = tmp_path / 'release-notes.json'
    path.write_text(document(rows))
    monkeypatch.setattr(release_notes, 'PATH', path)
    release_notes.bundled.cache_clear()
    try:
        # The old cached notice is superseded by a packaged ordinary record,
        # even though that record itself falls outside the displayed window.
        kept = release_notes.history([entry('0.0.1')])
        assert kept == [ordinary(f'0.1.{i}') for i in range(299, 43, -1)]
        assert not any(row['notices'] for row in kept)
    finally:
        release_notes.bundled.cache_clear()


def test_cached_notice_all_fields_survive_validation_selection_and_repeated_merge(monkeypatch):
    protected = entry('0.0.1')
    protected['extension'] = {'keep': [1, 2]}
    protected['date'] = '2026-11-17'
    protected['notices'][0].update(previousTitles=[], extension='unchanged')
    monkeypatch.setattr(release_notes, 'bundled', lambda: [])
    saved = release_notes.history([protected, *[ordinary(f'0.1.{i}') for i in range(300)]])
    assert saved[-1] == protected
    assert release_notes.history(saved) == saved


def test_valid_sources_with_too_many_combined_notices_fail_explicitly(monkeypatch):
    monkeypatch.setattr(release_notes, 'MAX_RELEASES', 3)
    monkeypatch.setattr(release_notes, 'bundled', lambda: [entry('0.1.0'), entry('0.1.1')])
    with pytest.raises(release_notes.HistoryOverflow, match='protected'):
        release_notes.history([entry('0.1.2'), entry('0.1.3')])


def test_missing_or_malformed_bundled_history_still_uses_damaged_app_fallback(tmp_path, monkeypatch):
    path = tmp_path / 'release-notes.json'
    monkeypatch.setattr(release_notes, 'PATH', path)
    for raw in (None, 'not JSON', document([{'invalid': True}])):
        if raw is not None:
            path.write_text(raw)
        release_notes.bundled.cache_clear()
        assert release_notes.bundled() == []
    release_notes.bundled.cache_clear()


def test_merged_utf8_pressure_protects_guidance_and_keeps_future_ceiling(monkeypatch):
    protected = entry('0.0.1')
    future = ordinary('99.0.0')
    future['changes'] = ['é' * 1200]
    old = ordinary('0.1.0')
    old['changes'] = ['é' * 1200]
    monkeypatch.setattr(release_notes, 'bundled', lambda: [protected, old])
    monkeypatch.setattr(release_notes, 'MAX_BYTES',
                        len(release_notes.serialize([future, protected]).encode('utf-8')))
    assert release_notes.history([future]) == [future, protected]
    assert release_notes.history([future], latest='v0.99.5') == [old, protected]


def test_overflow_is_not_swallowed_by_bundled_or_cache_fallback(tmp_path, monkeypatch):
    rows = [entry('0.1.0'), entry('0.1.1')]
    path = tmp_path / 'release-notes.json'
    path.write_text(document(rows))
    monkeypatch.setattr(release_notes, 'PATH', path)
    monkeypatch.setattr(release_notes, 'MAX_RELEASES', 1)
    release_notes.bundled.cache_clear()
    try:
        with pytest.raises(release_notes.HistoryOverflow):
            release_notes.bundled()
    finally:
        release_notes.bundled.cache_clear()
    monkeypatch.setattr(release_notes, 'bundled', lambda: [])
    with pytest.raises(release_notes.HistoryOverflow):
        release_notes.history(rows)


def test_unread_and_editorial_acknowledgements_unchanged_after_retention(monkeypatch):
    protected = entry('0.0.1')
    old_state = {'updates': {'application': {'releaseNotes': [protected]}}}
    old = snapshot(old_state)['items'][0]
    protected = copy.deepcopy(protected)
    protected['notices'][0].update(title='Editorial correction', previousTitles=[old['title']])
    monkeypatch.setattr(release_notes, 'bundled', lambda: [protected])
    state = {'attentionRead': {old['id']: old['fingerprint']},
             'updates': {'application': {'releaseNotes': release_notes.history(
                 [ordinary(f'0.1.{i}') for i in range(511)])}}}
    read_before = copy.deepcopy(state['attentionRead'])
    current = snapshot(state)['items'][0]
    assert current['id'] == old['id'] and current['read']
    state['updates']['application']['releaseNotes'][-1]['notices'][0]['action'] = 'Changed action'
    assert not snapshot(state)['items'][0]['read']
    assert state['attentionRead'] == read_before


@pytest.mark.parametrize('failure',['check','notes'])
async def test_failed_refresh_preserves_saved_upcoming_notices_across_restart(tmp_path,monkeypatch,failure,service_types):
    AppService, Runtime = service_types
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
