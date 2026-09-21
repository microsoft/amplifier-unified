"""Current compatible releases advance without weakening exact-artifact validation."""
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import terminal_release as releases
from amplifier_web.terminal_setup import PLATFORMS, TerminalSetup

SPEC = PLATFORMS['macos-arm64']


def build(version='0.9.0rc1', identity=10, protocol=1, **overrides):
    filename = f'amplifier_app_tui-{version}-py3-none-{SPEC["tag"]}.whl'
    wheel = ('fixture wheel ' + version).encode()
    digest = hashlib.sha256(wheel).hexdigest()
    receipt = dict(version=version, wheel=filename, wheel_sha256=digest,
                   platform='Darwin', architecture='arm64', source_commit='a' * 40,
                   tracked_source_clean=True, doctor_passed=True, state_untouched=True,
                   connected_install_without_execution_dependencies=True,
                   installed_native_bytes_match=True, installed_native_load_passed=True,
                   artifact_privacy_scan_passed=True, connected_protocol_version=protocol,
                   **overrides)
    encoded = json.dumps(receipt).encode()
    def asset(name, contents, asset_id):
        return dict(id=asset_id, name=name, size=len(contents), state='uploaded',
                    digest='sha256:' + hashlib.sha256(contents).hexdigest())
    row = dict(tag_name='v' + version, draft=False, prerelease='rc' in version,
               published_at='2026-09-21T00:00:00Z',
               assets=[asset(filename, wheel, identity), asset(filename + '.receipt.json', encoded, identity+1)])
    return row, {identity: wheel, identity+1: encoded}


def catalog(monkeypatch, rows, payloads):
    async def github(endpoint, **kwargs):
        if '/releases?' in endpoint:
            return json.dumps(rows).encode()
        return payloads[int(endpoint.rsplit('/', 1)[1])]
    mock = AsyncMock(side_effect=github)
    monkeypatch.setattr(releases, 'github', mock)
    return mock


async def test_new_setup_advances_to_latest_prerelease_and_caches_verified_bytes(tmp_path, monkeypatch):
    old, old_files = build('0.9.0', identity=10)
    new, new_files = build('0.10.0rc1', identity=20)
    rows = [old]
    transport = catalog(monkeypatch, rows, old_files | new_files)
    assert (await releases.latest_release(SPEC, tmp_path)).version == '0.9.0'
    rows.append(new)
    selected = await releases.latest_release(SPEC, tmp_path)
    assert selected.version == '0.10.0rc1' and selected.wheel == new_files[20]
    transport.reset_mock()
    assert await releases.latest_release(SPEC, tmp_path) == selected
    assert transport.await_count == 1  # fresh catalog, content-addressed verified cache
    (tmp_path / selected.sha256).write_bytes(b'broken cache')
    assert await releases.latest_release(SPEC, tmp_path) == selected
    assert transport.await_count == 3  # catalog + repaired wheel


async def test_selects_supported_platform_protocol_and_published_releases(tmp_path, monkeypatch):
    valid, files = build()
    protocol2, files2 = build('1.0.0', identity=20, protocol=2)
    legacy, files3 = build('0.10.0', identity=30, protocol=None)
    other_platform, _ = build('1.2.0', identity=40)
    other_platform['assets'] = []
    draft, _ = build('2.0.0', identity=50)
    draft['draft'] = True
    unpublished = dict(draft, draft=False, published_at=None)
    transport = catalog(monkeypatch, [draft, unpublished, valid, protocol2, legacy, other_platform], files | files2 | files3)
    assert (await releases.latest_release(SPEC, tmp_path)).version == '0.9.0rc1'
    assert not any(call.args[0].endswith('/20') for call in transport.await_args_list)


@pytest.mark.parametrize('damage', ['wheel', 'receipt', 'missing-receipt', 'missing-digest', 'oversize', 'duplicate'])
async def test_unverified_newest_candidate_never_silently_falls_back(tmp_path, monkeypatch, damage):
    row, files = build()
    old, old_files = build('0.8.0', identity=20)
    if damage == 'wheel':
        files[10] = b'bad wheel'
    elif damage == 'receipt':
        files[11] = b'bad receipt'
    elif damage == 'missing-receipt':
        row['assets'].pop()
    elif damage == 'missing-digest':
        del row['assets'][0]['digest']
    elif damage == 'oversize':
        row['assets'][0]['size'] = releases.MAX_WHEEL_BYTES + 1
    else:
        row['assets'].append(row['assets'][0])
    catalog(monkeypatch, [row, old], files | old_files)
    with pytest.raises(ValueError):
        await releases.latest_release(SPEC, tmp_path)


@pytest.mark.parametrize('key,value', [('tracked_source_clean', False), ('installed_native_load_passed', False),
                                     ('wheel_sha256', '0' * 64), ('architecture', 'x86_64'),
                                     ('version', '0.1.0'), ('source_commit', '../main'),
                                     ('state_untouched', 'true')])
async def test_receipt_must_qualify_the_exact_artifact(tmp_path, monkeypatch, key, value):
    row, files = build()
    receipt = json.loads(files[11])
    receipt[key] = value
    files[11] = json.dumps(receipt).encode()
    row['assets'][1].update(size=len(files[11]), digest='sha256:' + hashlib.sha256(files[11]).hexdigest())
    catalog(monkeypatch, [row], files)
    with pytest.raises(ValueError, match='qualification'):
        await releases.latest_release(SPEC, tmp_path)


async def test_no_compatible_release_reports_a_publication_blocker(tmp_path, monkeypatch):
    row, files = build(protocol=None)
    catalog(monkeypatch, [row], files)
    with pytest.raises(ValueError, match='No qualified terminal release'):
        await releases.latest_release(SPEC, tmp_path)


async def test_newest_release_can_be_on_a_later_catalog_page(tmp_path, monkeypatch):
    row, files = build()
    async def github(endpoint, **kwargs):
        if endpoint.endswith('&page=1'):
            return json.dumps([dict(tag_name='not-a-version')] * 100).encode()
        if endpoint.endswith('&page=2'):
            return json.dumps([row]).encode()
        return files[int(endpoint.rsplit('/', 1)[1])]
    monkeypatch.setattr(releases, 'github', github)
    assert (await releases.latest_release(SPEC, tmp_path)).version == '0.9.0rc1'


async def test_catalog_bound_fails_instead_of_claiming_an_older_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(releases, 'github', AsyncMock(return_value=json.dumps([{}] * 100).encode()))
    with pytest.raises(ValueError, match='lookup size'):
        await releases.latest_release(SPEC, tmp_path)


@pytest.mark.parametrize('tag', ['v../../bad', 'v1.0.0;exit', 'v1.0.0+local', 'v01.0.0', 'latest', None])
def test_only_canonical_filename_safe_versions_are_accepted(tag):
    assert releases.release_version(tag) is None


async def test_prepared_retry_survives_new_release_and_offline_catalog(tmp_path, monkeypatch):
    manager = TerminalSetup(tmp_path, ['https://service.example'])
    row, files = build()
    lookup = catalog(monkeypatch, [row], files)
    args = {'server': 'https://service.example', 'platform': 'macos-arm64', 'name': 'Fixture'}
    one = await manager.perform('terminal.prepare', args, 'original')
    path = manager.directory / (one['installer']['id'] + '.sh')
    original_bytes = path.read_bytes()
    lookup.side_effect = ValueError('offline')
    assert await manager.perform('terminal.prepare', args, 'original') == one
    assert path.read_bytes() == original_bytes
    with pytest.raises(ValueError, match='offline'):
        await manager.perform('terminal.prepare', args, 'new-request')
    assert len(manager.devices.read()['grants']) == 1


async def test_lookup_network_failure_is_bounded_and_does_not_create_setup(tmp_path, monkeypatch):
    manager = TerminalSetup(tmp_path, ['https://service.example'])
    monkeypatch.setattr(releases, 'github', AsyncMock(side_effect=ValueError('GitHub unavailable')))
    with pytest.raises(ValueError, match='unavailable'):
        await manager.perform('terminal.prepare', {'server': 'https://service.example',
            'platform': 'macos-arm64', 'name': 'Fixture'}, 'request')
    assert manager.devices.read()['grants'] == {}
    assert not list(manager.directory.glob('*.sh'))
