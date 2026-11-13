"""Automatic release intent is not an explicit saved bundle choice."""
import json
import os
from pathlib import Path
import time
import uuid

import pytest
import yaml
from amplifier_worktrees.git import atomic

from amplifier_web import bundle_defaults, workspace_provisioning as provisioning
from amplifier_web.bundle_selection import defaults
from amplifier_web.host.config import read_config
from amplifier_web.service import AppService
from amplifier_web.workspace_starters import StarterCatalog


def seed_legacy(tmp_path, *, inherited=False):
    """The exact old rev-8 generator shape with matching allocation evidence."""
    home, workspace = tmp_path / 'app', tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / '.amplifier').mkdir()
    starter = StarterCatalog(home).snapshot('amplifier-development')
    starter.pop('bundleDefault')
    starter.update(revision=8, bundle='' if inherited else 'anchors-amp-dev')
    identity = str(uuid.uuid4())
    info = workspace.stat()
    plan = {'planId': identity, 'path': str(workspace), 'starter': starter,
            'attempt': 'receipt-test.json'}
    placement = home / 'workspace-placement'
    placement.mkdir(parents=True)
    atomic(placement / (identity + '.json'), plan)
    atomic(placement / plan['attempt'], {**plan, 'outcome': 'created',
                                        'directoryIdentity': [info.st_dev, info.st_ino]})
    value = {'id': identity, 'revision': 3, 'path': str(workspace),
             'directoryIdentity': [info.st_dev, info.st_ino], 'starter': starter,
             'status': 'ready', 'scaffolded': True, 'files': [], 'repositories': []}
    if not inherited:
        (workspace / '.amplifier/settings.yaml').write_text(
            yaml.safe_dump({'bundle': {'active': starter['bundle']}}))
        value['files'].append({'path': '.amplifier/settings.yaml', 'status': 'created'})
    target = provisioning.receipt_path(home, identity)
    target.parent.mkdir()
    atomic(target, value)
    return home, workspace, identity


@pytest.mark.parametrize('inherited', [False, True])
def test_proven_legacy_default_follows_release_and_restart_is_idempotent(tmp_path, monkeypatch, inherited):
    home, workspace, identity = seed_legacy(tmp_path, inherited=inherited)
    value = provisioning.recover(home, identity)
    assert value['bundleDefaultMigration']['policy'] == 'release'
    path = workspace / '.amplifier/settings.yaml'
    assert yaml.safe_load(path.read_text()) == {'bundle': {'releaseDefault': 'amplifier-development'}}
    assert defaults(home, workspace)['workspace'] is None
    assert defaults(home, workspace)['effective'] == read_config(workspace, home=home).active_bundle == 'work-amp-dev'
    before = path.read_bytes(), provisioning.receipt_path(home, identity).read_bytes()
    assert provisioning.recover(home, identity) == value
    assert (path.read_bytes(), provisioning.receipt_path(home, identity).read_bytes()) == before
    monkeypatch.setitem(bundle_defaults.STARTER_BUNDLES, 'amplifier-development', 'next-release')
    assert defaults(home, workspace)['effective'] == read_config(workspace, home=home).active_bundle == 'next-release'
    assert path.read_bytes() == before[0]
    # No-workspace/managed readers never inherit the workspace marker.
    assert defaults(home, workspace, global_only=True)['effective'] == 'work'
    assert read_config(workspace, home=home, global_only=True).active_bundle == 'work'


@pytest.mark.parametrize('scope', ['project', 'local', 'app', 'shared'])
def test_explicit_same_as_old_default_wins_then_clear_reveals_release(tmp_path, scope):
    home, workspace, identity = seed_legacy(tmp_path)
    if scope == 'local':
        (workspace / '.amplifier/settings.local.yaml').write_text('bundle:\n  active: anchors-amp-dev\n')
    if scope == 'shared':
        from amplifier_web.session_files import amplifier_home
        shared = amplifier_home() / 'settings.yaml'
        shared.parent.mkdir(parents=True)
        shared.write_text('bundle:\n  active: anchors-amp-dev\n')
    provisioning.recover(home, identity)
    if scope == 'project':
        # An explicit project selection remains explicit even beside a marker.
        (workspace / '.amplifier/settings.yaml').write_text(
            'bundle:\n  active: anchors-amp-dev\n  releaseDefault: amplifier-development\n')
    app = 'anchors-amp-dev' if scope == 'app' else None
    assert defaults(home, workspace, app)['effective'] == 'anchors-amp-dev'
    if scope != 'app':
        assert read_config(workspace, home=home).active_bundle == 'anchors-amp-dev'
    if scope == 'project':
        (workspace / '.amplifier/settings.yaml').write_text('bundle:\n  releaseDefault: amplifier-development\n')
    elif scope == 'local':
        (workspace / '.amplifier/settings.local.yaml').write_text('{}\n')
    elif scope == 'shared':
        shared.write_text('{}\n')
    assert defaults(home, workspace)['effective'] == 'work-amp-dev'


@pytest.mark.parametrize('damage', [
    'manual-comment', 'extra-setting', 'different-value', 'preserved', 'custom',
    'unknown-revision', 'changed-instructions', 'null-instructions', 'null-starter', 'custom-source', 'missing-plan',
    'missing-allocation', 'conflicting-allocation', 'changed-directory', 'symlink',
    'null-plan', 'null-allocation', 'list-bundle', 'mapping-bundle',
])
def test_ambiguous_legacy_values_are_never_rewritten(tmp_path, damage):
    home, workspace, identity = seed_legacy(tmp_path)
    target = provisioning.receipt_path(home, identity)
    value = json.loads(target.read_text())
    path = workspace / '.amplifier/settings.yaml'
    placement = home / 'workspace-placement' / (identity + '.json')
    if damage == 'manual-comment':
        path.write_text(path.read_text() + '# explicit project choice\n')
    elif damage == 'extra-setting':
        path.write_text(path.read_text() + 'keep: true\n')
    elif damage == 'different-value':
        path.write_text('bundle:\n  active: work\n')
    elif damage == 'preserved':
        value['files'][0]['status'] = 'preserved'
    elif damage == 'custom':
        value['starter']['builtIn'] = False
    elif damage == 'unknown-revision':
        value['starter']['revision'] = 4
    elif damage == 'changed-instructions':
        value['starter']['instructions'] += '\nmanual'
    elif damage == 'null-instructions':
        value['starter']['instructions'] = None
    elif damage == 'null-starter':
        value['starter'] = None
    elif damage == 'list-bundle':
        value['starter']['bundle'] = []
    elif damage == 'mapping-bundle':
        value['starter']['bundle'] = {}
    elif damage == 'custom-source':
        value['starter']['bundleSource'] = 'git+https://example.test/root'
    elif damage == 'missing-plan':
        placement.unlink()
    elif damage == 'null-plan':
        atomic(placement, None)
    elif damage == 'null-allocation':
        atomic(placement.parent / 'receipt-test.json', None)
    elif damage == 'missing-allocation':
        (placement.parent / 'receipt-test.json').unlink()
    elif damage == 'conflicting-allocation':
        receipt = json.loads((placement.parent / 'receipt-test.json').read_text())
        receipt['directoryIdentity'] = [0, 0]
        atomic(placement.parent / 'receipt-test.json', receipt)
    elif damage == 'changed-directory':
        value['directoryIdentity'] = [0, 0]
    elif damage == 'symlink':
        other = workspace / 'manual.yaml'
        path.rename(other)
        path.symlink_to(other)
    atomic(target, value)
    before = path.read_bytes(), target.read_bytes()
    provisioning.recover(home, identity)
    assert (path.read_bytes(), target.read_bytes()) == before


def test_byte_identical_manual_rewrite_has_no_historical_intent_witness(tmp_path):
    home, workspace, identity = seed_legacy(tmp_path)
    path = workspace / '.amplifier/settings.yaml'
    # Old receipts have no edit lineage. Document, rather than pretend to solve,
    # this unavoidable ambiguity. UI same-value overrides have their own file.
    path.write_bytes(path.read_bytes())
    provisioning.recover(home, identity)
    assert 'active' not in yaml.safe_load(path.read_text())['bundle']


def test_nonregular_legacy_setting_refused_without_blocking(tmp_path):
    home, workspace, identity = seed_legacy(tmp_path)
    path = workspace / '.amplifier/settings.yaml'
    path.unlink()
    os.mkfifo(path)
    before = provisioning.receipt_path(home, identity).read_bytes()
    start = time.monotonic()
    provisioning.recover(home, identity)
    assert time.monotonic() - start < 1
    assert provisioning.receipt_path(home, identity).read_bytes() == before


async def test_startup_migrates_registered_workspace_not_existing_chat_or_draft(tmp_path, monkeypatch):
    from test_live_clients import command, snapshot
    home, workspace, identity = seed_legacy(tmp_path)
    app = AppService(home, workspace=workspace)
    try:
        await app.dispatch('session.create', {'bundle': 'anchors-amp-dev'})
        existing = app._session()['id']
        row = next(w for w in app.state['workspaces'] if w['path'] == str(workspace))
        row['setupId'] = identity
        app.clients.attach('web', kind='web')
        await command(app, 'web', 'session.draft')
        setup = {'workspace': str(workspace), 'bundle': 'anchors-amp-dev'}
        await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Keep my intent'}})
        app._save()
    finally:
        await app.close()
    app = AppService(home, workspace=workspace)
    try:
        assert app._session(existing)['bundle'] == 'anchors-amp-dev'
        assert snapshot(app, 'web')['view']['newSessionDraft'] == setup
        assert snapshot(app, 'web')['view']['draft'] == 'Keep my intent'
        assert app._new_session({})['bundle'] == 'work-amp-dev'
        assert app._new_session({'bundle': 'anchors-amp-dev'})['bundle'] == 'anchors-amp-dev'
        monkeypatch.setitem(bundle_defaults.STARTER_BUNDLES, 'amplifier-development', 'future-release')
        assert app._new_session({})['bundle'] == 'future-release'
        assert app._new_session({'bundle': 'anchors-amp-dev'})['bundle'] == 'anchors-amp-dev'
        assert app._session(existing)['bundle'] == 'anchors-amp-dev'
    finally:
        await app.close()


async def test_general_release_change_affects_new_managed_and_workspace_chats_not_explicit_choices(tmp_path, monkeypatch):
    from test_service import Runtime
    from test_live_clients import command
    app = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    app.clients.attach('web', kind='web')
    try:
        result = await command(app, 'web', 'session.create', {})
        old = result['sessionId']
        assert app._session(old)['bundle'] == 'work'
        monkeypatch.setattr(bundle_defaults, 'GENERAL_BUNDLE', 'next-general')
        result = await command(app, 'web', 'session.create', {})
        assert app._session(result['sessionId'])['bundle'] == 'next-general'
        result = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}})
        assert app._session(result['sessionId'])['bundle'] == 'next-general'
        result = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}, 'bundle': 'work'})
        assert app._session(result['sessionId'])['bundle'] == 'work'
        assert app._session(old)['bundle'] == 'work'
    finally:
        await app.close()


@pytest.mark.parametrize('explicit', [False, True])
async def test_attached_folder_has_no_automatic_provenance_and_is_preserved(tmp_path, explicit):
    home, initial, attached = tmp_path / 'app', tmp_path / 'initial', tmp_path / 'attached'
    initial.mkdir()
    attached.mkdir()
    path = attached / '.amplifier/settings.yaml'
    if explicit:
        path.parent.mkdir()
        path.write_text('bundle:\n  active: anchors-amp-dev\n')
    app = AppService(home, workspace=initial)
    try:
        await app.dispatch('workspace.add', {'path': str(attached)})
        assert app._new_session({})['bundle'] == ('anchors-amp-dev' if explicit else 'work')
        row = next(w for w in app.state['workspaces'] if w['path'] == str(attached))
        assert not row.get('setupId')
        assert path.exists() is explicit
        if explicit:
            assert path.read_text() == 'bundle:\n  active: anchors-amp-dev\n'
    finally:
        await app.close()


@pytest.mark.parametrize('scope', ['project', 'local', 'global'])
def test_resolvers_agree_on_release_intent_scope(tmp_path, scope):
    from amplifier_web.shared_settings import settings_paths
    workspace = tmp_path / 'work'
    workspace.mkdir()
    path = settings_paths(workspace)[scope]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('bundle:\n  releaseDefault: amplifier-development\n')
    assert defaults(tmp_path / 'app', workspace)['effective'] == read_config(workspace).active_bundle == 'work-amp-dev'


def test_read_only_defaults_have_constant_file_budget_no_receipt_scan(tmp_path, monkeypatch):
    home, workspace, identity = seed_legacy(tmp_path)
    from amplifier_web import shared_settings
    original, calls = shared_settings.read_yaml, []
    def read(path):
        calls.append(path)
        return original(path)
    def no_scan(*args, **kwargs):
        raise AssertionError('Default read must not scan or migrate receipts')
    monkeypatch.setattr(shared_settings, 'read_yaml', read)
    monkeypatch.setattr(Path, 'iterdir', no_scan)
    monkeypatch.setattr(provisioning, 'recover', no_scan)
    before = (workspace / '.amplifier/settings.yaml').read_bytes()
    for _ in range(10):
        assert defaults(home, workspace)['effective'] == 'anchors-amp-dev'
    assert len(calls) == 30  # Same three scope files, independent of saved receipts.
    assert (workspace / '.amplifier/settings.yaml').read_bytes() == before