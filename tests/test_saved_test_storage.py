"""Saved-size receipts never become current physical measurements or cleanup."""
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web import saved_test_storage as sizes
from amplifier_web import storage_cleanup


@pytest.fixture
def app(tmp_path):
    return SimpleNamespace(data_dir=tmp_path)


def row(identity='test-1'):
    return {'id': identity, 'sessionId': 'root', 'status': 'retired', 'kind': 'test-environment',
            'profileId': 'browser-test', 'createdAt': 1000, 'universeId': 'test-environment', 'snapshot': {'archive_sha256': 'a'*64},
            'lastOperation': {'resourceId': identity, 'sessionId': 'root', 'state': 'completed',
                'action': 'workspace.environments.remove', 'result': {'ok': True, 'status': 'removed', 'universeId': 'test-environment',
                    'retention': {'verified': True, 'policy': 'files', 'sourceSha256': 'a'*64, 'sourceBytes': 100, 'archiveBytes': 220, 'totalBytes': 320},
                    'archives': [{'id': 'test-environment', 'source': '/workspace', 'bytes': 200, 'sha256': 'b'*64, 'verified': True},
                                 {'id': 'test-environment', 'source': '/artifacts', 'bytes': 20, 'sha256': 'c'*64, 'verified': True}]}}}


def insert(app, value):
    folder = app.data_dir / 'workspace-resources'
    folder.mkdir(exist_ok=True)
    with sqlite3.connect(folder / 'reservations.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS allocations(id TEXT PRIMARY KEY,root TEXT,status TEXT,body TEXT)')
        db.execute('INSERT INTO allocations VALUES(?,?,?,?)', (value['id'], value['sessionId'], value['status'], json.dumps(value)))


def test_empty_read_creates_no_state(app):
    before = list(app.data_dir.iterdir())
    assert sizes.read(app)['items'] == []
    assert list(app.data_dir.iterdir()) == before


async def test_dispatched_read_needs_no_runtime_or_configuration_and_preserves_ledger(app):
    insert(app, row())
    ledger = app.data_dir / 'workspace-resources/reservations.sqlite3'
    before = ledger.read_bytes()
    result = await storage_cleanup.dispatch(app, 'storage.cleanup.evidence', {}, 'ui', None)
    item, = result['result']['items']
    assert (item['sourceBytes'], item['workspaceBytes'], item['resultBytes'], item['totalBytes']) == (100, 200, 20, 320)
    assert item['label'] == 'Browser check'
    assert ledger.read_bytes() == before
    assert 'current disk measurement' in result['result']['note']


def test_pages_are_bounded_and_new_rows_do_not_duplicate_earlier_page(app):
    for i in range(35):
        insert(app, row(f'test-{i}'))
    first = sizes.read(app)
    assert len(first['items']) == 32 and first['recordedBytes'] == 32 * 320
    insert(app, row('new-test'))
    second = sizes.read(app, first['nextBefore'])
    assert len(second['items']) == 3 and second['nextBefore'] is None
    assert not set(i['id'] for i in first['items']) & set(i['id'] for i in second['items'])


@pytest.mark.parametrize('change', ['active', 'wrong-root', 'wrong-resource', 'wrong-universe', 'wrong-source', 'unknown', 'unverified', 'negative', 'boolean', 'duplicate-role', 'wrong-total', 'missing-archive', 'incomplete-selection'])
def test_unaccounted_records_remain_visible_without_invented_bytes(app, change):
    value = row(); request = value['lastOperation']; result = request['result']
    if change == 'active': value['status'] = 'ready'
    if change == 'wrong-root': request['sessionId'] = 'peer'
    if change == 'wrong-resource': request['resourceId'] = 'peer-resource'
    if change == 'wrong-universe': result['universeId'] = 'other'
    if change == 'wrong-source': result['retention']['sourceSha256'] = 'b'*64
    if change == 'unknown': request['state'] = 'outcome_unknown'
    if change == 'unverified': result['retention']['verified'] = False
    if change == 'negative': result['retention']['sourceBytes'] = -1
    if change == 'boolean': result['retention']['sourceBytes'] = True
    if change == 'duplicate-role': result['archives'][1]['source'] = '/workspace'
    if change == 'wrong-total': result['retention']['totalBytes'] = 0
    if change == 'missing-archive': result.pop('archives')
    if change == 'incomplete-selection':
        result['archives'].pop();result['retention'].update(archiveBytes=200,totalBytes=300)
    insert(app, value)
    page = sizes.read(app)
    assert len(page['items']) == 1 and page['items'][0]['totalBytes'] is None
    assert page['unaccountedCount'] == 1 and page['recordedBytes'] == 0


def test_remote_receipt_is_not_double_counted_and_full_archive_stays_distinct(app):
    remote = row('remote'); result = remote['lastOperation']['result']
    remote['testMachine'] = {'id': 'remote'}
    result['downloadedArchives'] = copy.deepcopy(result['archives'])
    insert(app, remote)
    full = row('full'); result = full['lastOperation']['result']
    result['archives'] = [{'id': 'test-environment', 'source': '/', 'bytes': 220, 'sha256': 'd'*64, 'verified': True}]
    result['retention']['policy'] = 'full'
    insert(app, full)
    page = sizes.read(app)
    assert page['recordedBytes'] == 640
    assert page['items'][0]['otherArchiveBytes'] == 220
    assert page['items'][0]['workspaceBytes'] is None and page['items'][0]['combinedArchive']
    assert 'downloaded' in page['items'][1]['custody']


@pytest.mark.parametrize('change', ['foreign-id', 'missing-id', 'missing-hash', 'invalid-hash', 'oversized-id', 'oversized-hash'])
@pytest.mark.parametrize('downloaded', [False, True])
def test_archive_identity_is_required_for_original_and_downloaded_records(app, change, downloaded):
    value = row(); result = value['lastOperation']['result']
    if downloaded:
        result['downloadedArchives'] = copy.deepcopy(result['archives'])
    archive = result['downloadedArchives' if downloaded else 'archives'][0]
    if change == 'foreign-id': archive['id'] = 'another-environment'
    if change == 'missing-id': archive.pop('id')
    if change == 'missing-hash': archive.pop('sha256')
    if change == 'invalid-hash': archive['sha256'] = 'not-a-hash'
    if change == 'oversized-id': archive['id'] = 'x'*100000
    if change == 'oversized-hash': archive['sha256'] = 'a'*100000
    insert(app, value)
    page = sizes.read(app); item, = page['items']
    assert item['totalBytes'] is None and 'custody' not in item
    assert page['unaccountedCount'] == 1 and page['recordedBytes'] == 0
    assert len(json.dumps(page)) < 3000


@pytest.mark.parametrize('change', ['swapped-sizes', 'swapped-hashes', 'missing-original', 'invalid-original', 'missing-role', 'empty', 'null', 'object'])
def test_download_cannot_replace_or_contradict_original_exports(app, change):
    value = row(); result = value['lastOperation']['result']
    result['downloadedArchives'] = copy.deepcopy(result['archives'])
    downloads = result['downloadedArchives']
    if change == 'swapped-sizes': downloads[0]['bytes'], downloads[1]['bytes'] = downloads[1]['bytes'], downloads[0]['bytes']
    if change == 'swapped-hashes': downloads[0]['sha256'], downloads[1]['sha256'] = downloads[1]['sha256'], downloads[0]['sha256']
    if change == 'missing-original': result.pop('archives')
    if change == 'invalid-original': result['archives'][0]['id'] = 'another-environment'
    if change == 'missing-role': downloads.pop()
    if change == 'empty': result['downloadedArchives'] = []
    if change == 'null': result['downloadedArchives'] = None
    if change == 'object': result['downloadedArchives'] = {}
    insert(app, value)
    page = sizes.read(app); item, = page['items']
    assert item['totalBytes'] is None and 'custody' not in item
    assert page['unaccountedCount'] == 1 and page['recordedBytes'] == 0


def test_matching_downloads_are_compared_by_role_without_reading_archive_paths(app):
    value = row(); value['testMachine'] = {'id': 'remote'}
    result = value['lastOperation']['result']
    result['downloadedArchives'] = list(reversed(copy.deepcopy(result['archives'])))
    for archive in result['archives']:
        archive['destination'] = '/not-accessed/original/' + archive['source'].strip('/')
    for archive in result['downloadedArchives']:
        archive['destination'] = '/not-accessed/download/' + archive['source'].strip('/')
    insert(app, value)
    page = sizes.read(app); item, = page['items']
    assert (item['sourceBytes'], item['workspaceBytes'], item['resultBytes'], item['totalBytes']) == (100, 200, 20, 320)
    assert 'downloaded' in item['custody'] and page['accountedCount'] == 1
    assert '/not-accessed' not in json.dumps(page)


def test_unrelated_large_metadata_and_malformed_label_are_not_returned(app):
    value = row();value['profileId'] = {'not': 'a label'}
    value['lastOperation']['result']['retention']['private'] = 'x'*100000
    insert(app, value)
    page = sizes.read(app)
    assert page['items'][0]['label'] == 'Saved test' and page['recordedBytes'] == 320
    assert len(json.dumps(page)) < 3000 and 'private' not in json.dumps(page)


def test_oversized_ledger_columns_stay_unavailable_and_bounded(app):
    value = row('r'*100000);value.update(status='s'*100000,sessionId='o'*100000)
    insert(app, value)
    page = sizes.read(app)
    assert page['items'][0]['id'] == 'Unavailable reference'
    assert page['items'][0]['totalBytes'] is None and page['unaccountedCount'] == 1
    assert len(json.dumps(page)) < 2000


def test_ledger_alias_is_refused_without_reading_target(app, tmp_path):
    other = tmp_path / 'other'; other.mkdir()
    (app.data_dir / 'workspace-resources').symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='could not be read'):
        sizes.read(app)


@pytest.mark.parametrize('cursor', [0, -1, True, '1', 2**53])
def test_invalid_cursor_is_rejected(app, cursor):
    with pytest.raises(ValueError, match='valid saved-test page'):
        sizes.read(app, cursor)
