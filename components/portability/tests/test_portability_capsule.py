import base64
import copy
import json
import os
from pathlib import Path
import stat

import pytest

from amplifier_portability import capsule
from amplifier_worktrees.git import digest, git, snapshot


def repository(tmp_path, name='source'):
    root = tmp_path / name
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.txt').write_text('original\n')
    (root / '.gitignore').write_text('ignored.txt\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'base')
    return root


def capture(root, mode='clean'):
    return capsule.capture_workspace(root, snapshot(root)[0]['sourceRevision'], mode)


def clone(root, tmp_path):
    destination = tmp_path / 'destination'
    git(tmp_path, 'clone', '--no-hardlinks', str(root), str(destination))
    return destination


def test_clean_roundtrip_uses_exact_commit_and_exports_no_source_paths(tmp_path):
    root = repository(tmp_path)
    source = snapshot(root)[0]
    payload = capture(root)
    assert str(root) not in json.dumps(payload)
    assert set(payload) == {'schemaVersion', 'head', 'sourceRevision', 'mode', 'stagedPatch', 'unstagedPatch',
                            'untracked', 'ignoredFilesIncluded', 'filterPolicy', 'reviewRequired'}
    destination = clone(root, tmp_path)
    # A destination's current HEAD can differ, provided the exact source commit exists.
    git(destination, 'config', 'user.name', 'Fixture')
    git(destination, 'config', 'user.email', 'fixture@example.invalid')
    (destination / 'other.txt').write_text('destination local base')
    git(destination, 'add', '.')
    git(destination, 'commit', '-m', 'later destination')
    target = tmp_path / 'managed' / 'task'
    result = capsule.restore_workspace(payload, destination, target)
    assert result['workingDirectory'] == str(target)
    assert result['head'] == source['head']
    assert result['sourceRevision'] == source['sourceRevision']
    assert git(target, 'branch', '--show-current') == b''
    assert (target / 'a.txt').read_text() == 'original\n'
    assert not (target / 'other.txt').exists()
    assert snapshot(root)[0]['sourceRevision'] == source['sourceRevision']


def test_carry_dirty_preserves_staged_unstaged_untracked_and_source(tmp_path):
    root = repository(tmp_path)
    destination = clone(root, tmp_path)
    (root / 'a.txt').write_text('staged\n')
    git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'untracked space.txt').write_bytes(b'raw\x00bytes\n')
    (root / 'untracked space.txt').chmod(0o755)
    (root / 'ignored.txt').write_text('excluded local private data')
    original = snapshot(root)[0]
    index = (root / '.git' / 'index').read_bytes()
    payload = capture(root, 'carry_dirty')
    target = tmp_path / 'managed' / 'task'
    result = capsule.restore_workspace(payload, destination, target)
    assert git(target, 'show', ':a.txt') == b'staged\n'
    assert (target / 'a.txt').read_text() == 'staged\nunstaged\n'
    assert (target / 'untracked space.txt').read_bytes() == b'raw\x00bytes\n'
    assert stat.S_IMODE((target / 'untracked space.txt').stat().st_mode) == 0o755
    assert not (target / 'ignored.txt').exists()
    assert payload['ignoredFilesIncluded'] is False
    assert result['sourceRevision'] == original['sourceRevision']
    assert snapshot(root)[0]['sourceRevision'] == original['sourceRevision']
    assert (root / '.git' / 'index').read_bytes() == index
    assert (root / 'ignored.txt').read_text() == 'excluded local private data'


def test_clean_rejects_dirty_and_stale_source(tmp_path):
    root = repository(tmp_path)
    revision = snapshot(root)[0]['sourceRevision']
    (root / 'a.txt').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        capsule.capture_workspace(root, revision)
    with pytest.raises(ValueError, match='clean source'):
        capture(root)


def test_source_changed_during_capture_is_rejected(tmp_path, monkeypatch):
    root = repository(tmp_path)
    (root / 'loose.txt').write_text('first')
    revision = snapshot(root)[0]['sourceRevision']
    original_read = capsule._regular_bytes
    def mutate(path, *args):
        result = original_read(path, *args)
        (root / 'a.txt').write_text('concurrent writer')
        return result
    monkeypatch.setattr(capsule, '_regular_bytes', mutate)
    with pytest.raises(ValueError, match='changed during capture'):
        capsule.capture_workspace(root, revision, 'carry_dirty')


def test_missing_or_wrong_commit_does_not_create_destination(tmp_path):
    root = repository(tmp_path)
    payload = capture(root)
    destination = repository(tmp_path, 'unrelated')
    (destination / 'a.txt').write_text('different\n')
    git(destination, 'commit', '-am', 'different')
    payload['head'] = 'a' * 40
    with pytest.raises(ValueError):
        capsule.restore_workspace(payload, destination, tmp_path / 'missing-head')
    assert not (tmp_path / 'missing-head').exists()
    payload['head'] = git(destination, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(ValueError, match='revision differs'):
        capsule.restore_workspace(payload, destination, tmp_path / 'wrong-head')
    assert (tmp_path / 'wrong-head').is_dir()  # Preserved for review, never reused or replayed.


@pytest.mark.parametrize('kind', ['untracked', 'tracked', 'parent'])
def test_source_symlinks_are_rejected(tmp_path, kind):
    root = repository(tmp_path)
    if kind == 'parent':
        alias = tmp_path / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        with pytest.raises(ValueError, match='Symbolic'):
            capsule.capture_workspace(alias, snapshot(root)[0]['sourceRevision'])
    else:
        link = root / 'link'
        link.symlink_to('a.txt')
        if kind == 'tracked':
            git(root, 'add', 'link')
            git(root, 'commit', '-m', 'symlink')
        with pytest.raises(ValueError, match='Symbolic'):
            capture(root, 'carry_dirty')


def test_submodule_conflicts_and_index_flags_are_rejected(tmp_path):
    root = repository(tmp_path)
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    git(root, 'update-index', '--add', '--cacheinfo', f'160000,{head},submodule')
    with pytest.raises(ValueError, match='Submodule'):
        capture(root, 'carry_dirty')
    git(root, 'reset', '--hard', 'HEAD')
    git(root, 'update-index', '--assume-unchanged', 'a.txt')
    with pytest.raises(ValueError, match='index flags'):
        capture(root)
    git(root, 'update-index', '--no-assume-unchanged', 'a.txt')
    git(root, 'checkout', '-b', 'other')
    (root / 'a.txt').write_text('other\n')
    git(root, 'commit', '-am', 'other')
    git(root, 'checkout', 'main')
    (root / 'a.txt').write_text('main\n')
    git(root, 'commit', '-am', 'main')
    with pytest.raises(ValueError):
        git(root, 'merge', 'other')
    with pytest.raises(ValueError, match='index conflicts'):
        capture(root, 'carry_dirty')


@pytest.mark.parametrize('name', ['../outside', '/outside', '.git/config', 'nested/../../outside',
                                'nested\\outside', 'a//b', 'a/./b', '.GIT/config', 'bad\nname'])
def test_untrusted_paths_are_rejected_before_creation(tmp_path, name):
    root = repository(tmp_path)
    payload = capture(root)
    payload['mode'] = 'carry_dirty'
    payload['untracked'] = [{'path': name, 'data': 'b2s=', 'sha256': digest(b'ok'), 'bytes': 2, 'mode': 0o644}]
    target = tmp_path / 'unsafe'
    with pytest.raises(ValueError, match='Unsafe'):
        capsule.restore_workspace(payload, root, target)
    assert not target.exists()


def test_traversal_and_symlink_patches_are_rejected_before_creation(tmp_path):
    root = repository(tmp_path)
    payload = capture(root)
    payload['mode'] = 'carry_dirty'
    patch = b'diff --git a/../escape b/../escape\nnew file mode 100644\n--- /dev/null\n+++ b/../escape\n@@ -0,0 +1 @@\n+escape\n'
    payload['stagedPatch'] = base64.b64encode(patch).decode()
    with pytest.raises(ValueError):
        capsule.restore_workspace(payload, root, tmp_path / 'unsafe')
    assert not (tmp_path / 'unsafe').exists()
    patch = b'diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+outside\n'
    payload['stagedPatch'] = base64.b64encode(patch).decode()
    with pytest.raises(ValueError, match='Symbolic'):
        capsule.restore_workspace(payload, root, tmp_path / 'link-unsafe')
    assert not (tmp_path / 'link-unsafe').exists()


def test_destination_must_be_fresh_outside_and_not_symlinked(tmp_path):
    root = repository(tmp_path)
    payload = capture(root)
    with pytest.raises(ValueError, match='fresh'):
        capsule.restore_workspace(payload, root, root)
    with pytest.raises(ValueError, match='outside'):
        capsule.restore_workspace(payload, root, root / 'nested')
    parent = tmp_path / 'managed'
    parent.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(parent, target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic'):
        capsule.restore_workspace(payload, root, link / 'target')
    assert not (parent / 'target').exists()


def test_aggregate_bytes_and_file_count_are_bounded(tmp_path, monkeypatch):
    root = repository(tmp_path)
    (root / 'one.txt').write_text('12345678')
    (root / 'two.txt').write_text('12345678')
    monkeypatch.setattr(capsule, 'MAX_BYTES', 12)
    with pytest.raises(ValueError, match='aggregate'):
        capture(root, 'carry_dirty')
    monkeypatch.setattr(capsule, 'MAX_BYTES', 1024)
    monkeypatch.setattr(capsule, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='Too many'):
        capture(root, 'carry_dirty')


@pytest.mark.parametrize('value', [
    {'apiKey': 'local-value'}, {'config': {'access_token': 'local-value'}},
    {'history': [{'text': 'Authorization: Bearer ' + 'a' * 20}]},
    {'text': '-----BEGIN RSA PRIVATE KEY-----'},
    {'text': 'sk-proj-' + 'a' * 24}, {'text': 'OPENAI_API_KEY=localvalue'},
    {'remote': 'https://user:password@example.invalid/repo'},
])
def test_public_data_rejects_known_credentials_without_redaction(value):
    original = copy.deepcopy(value)
    with pytest.raises(ValueError, match='credential'):
        capsule.validate_public(value)
    assert value == original


def test_secret_file_and_patch_rejected_and_no_capsule_written(tmp_path):
    root = repository(tmp_path)
    (root / 'secret.txt').write_text('Bearer ' + 'a' * 20)
    with pytest.raises(ValueError, match='credential'):
        capture(root, 'carry_dirty')
    (root / 'secret.txt').unlink()
    (root / 'a.txt').write_text('api_key = localvalue\n')
    git(root, 'add', 'a.txt')
    with pytest.raises(ValueError, match='credential'):
        capture(root, 'carry_dirty')
    destination = tmp_path / 'capsule.json'
    with pytest.raises(ValueError, match='credential'):
        capsule.write_capsule(destination, {'password': 'localvalue'})
    assert not destination.exists()


def test_private_atomic_capsule_roundtrip_and_corruption_rejected(tmp_path, monkeypatch):
    destination = tmp_path / 'capsules' / 'task.json'
    value = {'history': [{'role': 'user', 'text': 'Retain this exact history'}], 'credentialIntent': 'destination-owned'}
    written = capsule.write_capsule(destination, value)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert written['sha256'] == digest(destination.read_bytes())
    assert capsule.read_capsule(destination) == value
    destination.write_text('{"history": [], "history": [1]}')
    with pytest.raises(ValueError, match='Duplicate'):
        capsule.read_capsule(destination)
    destination.write_text('{"a": "' + 'x' * 100 + '"}')
    monkeypatch.setattr(capsule, 'MAX_CAPSULE_BYTES', 50)
    with pytest.raises(ValueError, match='size limit'):
        capsule.read_capsule(destination)


def test_capsule_read_write_reject_links_and_leave_original_untouched(tmp_path):
    original = tmp_path / 'original.json'
    original.write_text('{"keep": true}')
    link = tmp_path / 'link.json'
    link.symlink_to(original)
    for operation in [lambda: capsule.read_capsule(link), lambda: capsule.write_capsule(link, {'changed': True})]:
        with pytest.raises(ValueError, match='Symbolic'):
            operation()
    assert original.read_text() == '{"keep": true}'


def test_git_filters_hooks_and_external_diff_never_execute(tmp_path):
    root = repository(tmp_path)
    marker = tmp_path / 'must-not-run'
    command = f'touch {marker}'
    git(root, 'config', 'filter.danger.clean', command)
    git(root, 'config', 'filter.danger.smudge', command)
    git(root, 'config', 'filter.danger.process', command)
    git(root, 'config', 'filter.danger.required', 'true')
    git(root, 'config', 'diff.external', command)
    (root / '.gitattributes').write_text('*.txt filter=danger\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'attributes')
    hook = root / '.git' / 'hooks' / 'post-checkout'
    hook.write_text('#!/bin/sh\n' + command + '\n')
    hook.chmod(0o755)
    (root / 'a.txt').write_text('raw changed\n')
    git(root, 'add', 'a.txt')
    payload = capture(root, 'carry_dirty')
    result = capsule.restore_workspace(payload, root, tmp_path / 'managed')
    assert (Path(result['workingDirectory']) / 'a.txt').read_text() == 'raw changed\n'
    assert not marker.exists()


def test_rename_and_binary_changes_restore_exact_revision(tmp_path):
    root = repository(tmp_path)
    git(root, 'mv', 'a.txt', 'a renamed.txt')
    (root / 'binary.bin').write_bytes(bytes(range(256)) * 10)
    git(root, 'add', 'binary.bin')
    payload = capture(root, 'carry_dirty')
    result = capsule.restore_workspace(payload, root, tmp_path / 'renamed')
    assert result['sourceRevision'] == payload['sourceRevision']
    assert (tmp_path / 'renamed' / 'binary.bin').read_bytes() == bytes(range(256)) * 10


def test_binary_secret_is_scanned_before_export(tmp_path):
    root = repository(tmp_path)
    (root / 'binary.bin').write_bytes(b'\x00prefix\x00Bearer ' + b'a' * 30 + b'\x00suffix')
    git(root, 'add', 'binary.bin')
    with pytest.raises(ValueError, match='credential'):
        capture(root, 'carry_dirty')


def test_binary_expansion_limit_is_enforced(tmp_path, monkeypatch):
    root = repository(tmp_path)
    (root / 'binary.bin').write_bytes(b'\x00' * 4096)
    git(root, 'add', 'binary.bin')
    monkeypatch.setattr(capsule, 'MAX_BYTES', 1024)
    with pytest.raises(ValueError, match='expanded size'):
        capture(root, 'carry_dirty')


def test_partial_clone_is_rejected_before_any_snapshot(tmp_path, monkeypatch):
    root = repository(tmp_path)
    revision = snapshot(root)[0]['sourceRevision']
    git(root, 'config', 'remote.origin.promisor', 'true')
    monkeypatch.setattr(capsule, 'snapshot', lambda *_: pytest.fail('Must reject before lazy fetching is possible'))
    with pytest.raises(ValueError, match='partial clones'):
        capsule.capture_workspace(root, revision)


def test_git_directory_symlink_is_rejected(tmp_path):
    root = repository(tmp_path)
    revision = snapshot(root)[0]['sourceRevision']
    moved = tmp_path / 'git-data'
    (root / '.git').rename(moved)
    (root / '.git').symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic'):
        capsule.capture_workspace(root, revision)


def test_unicode_filenames_and_header_like_content_preserve_revision(tmp_path):
    root = repository(tmp_path)
    (root / 'a.txt').write_text('-- pretend path\nordinary\n')
    git(root, 'commit', '-am', 'header-like source')
    (root / 'a.txt').write_text('replacement\nordinary\n')
    (root / 'caf\u00e9.txt').write_text('unicode filename\n')
    (root / 'readonly.txt').write_text('retain read-only mode\n')
    (root / 'readonly.txt').chmod(0o440)
    git(root, 'add', 'caf\u00e9.txt')
    payload = capture(root, 'carry_dirty')
    result = capsule.restore_workspace(payload, root, tmp_path / 'unicode')
    assert result['sourceRevision'] == payload['sourceRevision']
    assert stat.S_IMODE((Path(result['workingDirectory']) / 'readonly.txt').stat().st_mode) == 0o440


def test_empty_credentials_are_allowed_but_non_json_and_nan_are_rejected(tmp_path):
    value = {'apiKey': None, 'credentials': {}, 'token': '', 'history': []}
    path = tmp_path / 'empty-credentials.json'
    capsule.write_capsule(path, value)
    assert capsule.read_capsule(path) == value
    for malformed in [{'bad': object()}, {'bad': float('nan')}, {'bad': float('inf')}]:
        with pytest.raises(ValueError):
            capsule.validate_public(malformed)


def test_binary_delta_roundtrip_preserves_staging(tmp_path):
    root = repository(tmp_path)
    (root / 'binary.bin').write_bytes(bytes(range(256)) * 64)
    git(root, 'add', 'binary.bin')
    git(root, 'commit', '-m', 'binary base')
    (root / 'binary.bin').write_bytes(bytes(range(256)) * 63 + b'changed final bytes\x00')
    git(root, 'add', 'binary.bin')
    payload = capture(root, 'carry_dirty')
    assert b'delta ' in base64.b64decode(payload['stagedPatch'])
    result = capsule.restore_workspace(payload, root, tmp_path / 'delta')
    assert result['sourceRevision'] == payload['sourceRevision']


def test_tiny_binary_delta_cannot_request_unbounded_destination(tmp_path):
    import zlib
    root = repository(tmp_path)
    payload = capture(root)
    size, encoded_size = capsule.MAX_BYTES + 1, bytearray()
    while size >= 128:
        encoded_size.append((size & 127) | 128)
        size >>= 7
    encoded_size.append(size)
    raw = b'\x00' + encoded_size
    compressed = zlib.compress(raw)
    length = len(compressed)
    prefix = bytes([length + (64 if length <= 26 else 70)])
    padded = compressed + b'\x00' * (-length % 4)
    patch = b'GIT binary patch\ndelta ' + str(len(raw)).encode() + b'\n' + prefix + base64.b85encode(padded) + b'\n\n'
    payload['mode'] = 'carry_dirty'
    payload['stagedPatch'] = base64.b64encode(patch).decode()
    with pytest.raises(ValueError, match='Binary delta exceeds'):
        capsule.restore_workspace(payload, root, tmp_path / 'bomb')
    assert not (tmp_path / 'bomb').exists()


@pytest.mark.parametrize('operation', ['staged', 'untracked', 'delete', 'rename'])
def test_host_scoped_amplifier_changes_never_transfer(tmp_path, operation):
    root = repository(tmp_path)
    settings = root / '.amplifier' / 'settings.yaml'
    settings.parent.mkdir()
    settings.write_text('provider: destination-owned\n')
    if operation in {'delete', 'rename'}:
        git(root, 'add', '.amplifier')
        git(root, 'commit', '-m', 'base local settings')
        if operation == 'delete':
            git(root, 'rm', str(settings.relative_to(root)))
        else:
            git(root, 'mv', str(settings.relative_to(root)), 'renamed.yaml')
    elif operation == 'staged':
        git(root, 'add', '.amplifier')
    with pytest.raises(ValueError, match='Host-scoped .amplifier'):
        capture(root, 'carry_dirty')


@pytest.mark.parametrize('path', ['.env', '.env.local', '.ssh/config', '.aws/config', 'credentials.json', '.docker/config.json'])
def test_untracked_credential_paths_are_excluded_even_without_secret_values(tmp_path, path):
    root = repository(tmp_path)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('innocent placeholder\n')
    with pytest.raises(ValueError, match='credential file paths'):
        capture(root, 'carry_dirty')


def test_preprovisioned_base_settings_do_not_become_transported_changes(tmp_path):
    root = repository(tmp_path)
    settings = root / '.amplifier' / 'settings.yaml'
    settings.parent.mkdir()
    settings.write_text('provider: destination-owned\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'preprovisioned settings')
    payload = capture(root)
    assert payload['stagedPatch'] == payload['unstagedPatch'] == ''
    result = capsule.restore_workspace(payload, root, tmp_path / 'base-settings')
    assert (Path(result['workingDirectory']) / '.amplifier' / 'settings.yaml').read_text() == settings.read_text()


def test_untrusted_patch_cannot_copy_settings_into_ordinary_path(tmp_path):
    root = repository(tmp_path)
    payload = capture(root)
    payload['mode'] = 'carry_dirty'
    patch = b'diff --git a/.amplifier/settings.yaml b/copy.yaml\nsimilarity index 100%\ncopy from .amplifier/settings.yaml\ncopy to copy.yaml\n'
    payload['stagedPatch'] = base64.b64encode(patch).decode()
    with pytest.raises(ValueError, match='Host-scoped .amplifier'):
        capsule.restore_workspace(payload, root, tmp_path / 'copied-settings')
    assert not (tmp_path / 'copied-settings').exists()


def test_replacement_refs_cannot_change_tree_under_exact_head(tmp_path):
    root = repository(tmp_path)
    payload = capture(root)
    old_head = payload['head']
    (root / 'a.txt').write_text('different base tree\n')
    git(root, 'commit', '-am', 'replacement')
    replacement = git(root, 'rev-parse', 'HEAD').decode().strip()
    git(root, 'replace', old_head, replacement)
    with pytest.raises(ValueError, match='replacement refs'):
        capsule.restore_workspace(payload, root, tmp_path / 'replaced')
    assert not (tmp_path / 'replaced').exists()
    with pytest.raises(ValueError, match='replacement refs'):
        capture(root)


@pytest.mark.parametrize('value', [{'secret_key': 'hidden'}, {'githubToken': 'hidden'}, {'text': 'secret_key=hidden'}])
def test_provider_named_tokens_and_secret_keys_are_rejected(value):
    with pytest.raises(ValueError, match='credential'):
        capsule.validate_public(value)
