"""Normal-caller bootstrap joins against the whole real Foundation owner API."""
from contextlib import closing
from pathlib import Path
import stat
import sys
import threading

import pytest

from amplifier_foundation.session.transcript_lifetime import (
    VersionedSessionBinding, VersionedSessionConflict,
)
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_web.host.canonical_bootstrap import SourceBootstrap
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_files import sessions_dir

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Prospective custody qualifies Linux only.')


class StartGuard:
    """Mutable live admission, not a persisted enrollment/permission flag."""
    def __init__(self):
        self.stopped = False
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.stopped:
            raise RuntimeError('Application stopped or shutting down')


def snapshot(directory):
    """Small synthetic namespace witness, including modes and physical identity."""
    return {str(path.relative_to(directory)): (
        path.lstat().st_ino, path.lstat().st_mode,
        path.read_bytes() if path.is_file() and not path.is_symlink() else None)
        for path in [directory, *sorted(directory.rglob('*'))]}


def test_new_root_is_fenced_before_store_construction(tmp_path):
    workspace = tmp_path / 'workspace'
    root = sessions_dir(workspace) / 'root'
    guard = StartGuard()
    assert not root.exists()
    selected = SourceBootstrap.acquire(workspace, 'root', admit=guard)
    with closing(selected.binding) as binding:
        assert type(binding) is VersionedSessionBinding
        assert binding.directory == root and binding.session_id == 'root'
        assert selected.resumed is False and callable(selected.child_factory)
        assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700
        assert guard.calls >= 3
        for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
            assert (root / name).is_dir() and list((root / name).iterdir())
            with pytest.raises(IsADirectoryError):
                (root / name).open('ab')
        with open_committed_source(root) as source:
            assert source.commit == binding.expected
            assert source.commit.message_count == 0
        # The normal shared store is downstream of acquisition, never its owner.
        store = SessionStore.for_app(tmp_path / 'home', workspace, canonical_binding=binding)
        assert store.load('root')[0] == []


@pytest.mark.parametrize('contents', [None, b'{"role":"user","content":"old"}\n'])
def test_occupied_legacy_root_is_probe_only_even_if_empty(tmp_path, contents):
    workspace = tmp_path / 'workspace'
    root = sessions_dir(workspace) / 'legacy'
    root.mkdir(parents=True, mode=0o755)
    if contents is not None:
        (root / 'transcript.jsonl').write_bytes(contents)
    (root / 'metadata.json').write_text('{"canonical":true,"versioned":true}')
    before = snapshot(root.parent)
    selected = SourceBootstrap.acquire(workspace, 'legacy', admit=StartGuard())
    assert selected.binding is None and selected.child_factory is None
    assert snapshot(root.parent) == before


def test_enrolled_resume_reads_genuine_exact_prefix_and_competing_owner_refuses(tmp_path):
    workspace = tmp_path / 'workspace'
    selected = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    with closing(selected.binding) as binding:
        binding.append([{'role': 'user', 'content': 'retain'}], expected=binding.expected,
                       operation_id='seed', admit=StartGuard())
        with open_committed_source(binding.directory) as source:
            expected = source.commit
        before = snapshot(binding.directory)
        with pytest.raises(VersionedSessionConflict, match='retains'):
            SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
        assert snapshot(binding.directory) == before
    resumed = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    with closing(resumed.binding) as binding:
        assert resumed.resumed is True
        assert binding.expected == expected
        assert binding.load_messages() == [{'role': 'user', 'content': 'retain'}]


@pytest.mark.parametrize('damage', ['missing-enrollment', 'changed-enrollment', 'invalid-source',
                                    'changed-lease', 'copied-root', 'missing-fence'])
def test_versioned_without_valid_enrollment_never_downgrades_or_adopts(tmp_path, damage):
    workspace = tmp_path / 'workspace'
    selected = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    root = selected.binding.directory
    selected.binding.close()
    if damage == 'missing-enrollment':
        (root / '.versioned-enrollment.json').unlink()
    elif damage == 'changed-enrollment':
        (root / '.versioned-enrollment.json').write_text('{"enabled":true}')
    elif damage == 'changed-lease':
        (root / '.versioned-owner').write_text('not-the-enrolled-lease-token')
    elif damage == 'copied-root':
        from shutil import copytree
        retained = root.with_name('retained-root')
        root.rename(retained)
        copytree(retained, root)
    elif damage == 'missing-fence':
        (root / 'transcript.jsonl.backup' / 'format.json').unlink()
    else:
        (root / 'transcript.v2' / 'current.json').write_text('{}')
    before = snapshot(root)
    with pytest.raises((ValueError, OSError)):
        SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    assert snapshot(root) == before


def test_bare_untrusted_versioned_marker_is_refusal_not_legacy(tmp_path):
    workspace = tmp_path / 'workspace'
    root = sessions_dir(workspace) / 'root'
    (root / 'transcript.v2').mkdir(parents=True, mode=0o700)
    (root / 'transcript.jsonl').write_text('old transcript must not become authority')
    before = snapshot(root)
    with pytest.raises((ValueError, OSError)):
        SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    assert snapshot(root) == before


def test_new_path_race_refuses_without_adopting_competitor_or_replaying(tmp_path):
    workspace = tmp_path / 'workspace'
    root = sessions_dir(workspace) / 'root'
    entered, occupied = threading.Event(), threading.Event()
    failures = []

    def competitor():
        try:
            assert entered.wait(10)
            root.mkdir(mode=0o700)
            (root / 'original').write_bytes(b'competing owner')
        except BaseException as error:
            failures.append(error)
        finally:
            occupied.set()

    def admit():
        if list(root.parent.glob('.versioned-new-*')):
            entered.set()
            assert occupied.wait(10)

    thread = threading.Thread(target=competitor)
    thread.start()
    try:
        with pytest.raises(FileExistsError):
            SourceBootstrap.acquire(workspace, 'root', admit=admit)
    finally:
        thread.join(11)
    assert not thread.is_alive() and not failures
    assert list(root.iterdir()) == [root / 'original']
    assert (root / 'original').read_bytes() == b'competing owner'
    assert len(list(root.parent.glob('.versioned-new-*'))) == 1


def test_prefix_change_between_read_and_lease_is_not_retried(tmp_path):
    workspace = tmp_path / 'workspace'
    selected = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    root, original = selected.binding.directory, selected.binding.expected
    selected.binding.close()
    calls = 0
    changed = None

    def admit():
        nonlocal calls, changed
        calls += 1
        # acquire entry, _acquire entry, then the genuine source has closed.
        if calls == 3:
            with closing(VersionedSessionBinding.resume(root, session_id='root', expected=original)) as owner:
                changed = owner.append([{'role': 'user', 'content': 'race winner'}],
                                       expected=owner.expected, operation_id='winner', admit=StartGuard()).commit

    with pytest.raises(ValueError, match='Expected committed source differs'):
        SourceBootstrap.acquire(workspace, 'root', admit=admit)
    assert calls == 3 and changed is not None
    with open_committed_source(root) as source:
        assert source.commit == changed
        assert source.window(0, 1).messages == [{'role': 'user', 'content': 'race winner'}]


def test_child_factory_new_resume_contention_and_no_legacy_conversion(tmp_path):
    workspace = tmp_path / 'workspace'
    selected = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    with closing(selected.binding) as root:
        child_path = root.directory.parent / 'child'
        factory = selected.child_factory
        with closing(factory(child_path, session_id='child', resumed=False)) as child:
            child.append([{'role': 'user', 'content': 'child context'}], expected=child.expected,
                         operation_id='child-seed', admit=StartGuard())
            expected = child.expected
            assert child.directory != root.directory
            with pytest.raises(VersionedSessionConflict, match='retains'):
                factory(child_path, session_id='child', resumed=True)
            with pytest.raises(FileExistsError):
                factory(child_path, session_id='child', resumed=False)
        with closing(factory(child_path, session_id='child', resumed=True)) as child:
            assert child.expected == expected
            assert child.load_messages() == [{'role': 'user', 'content': 'child context'}]
        legacy = root.directory.parent / 'legacy-child'
        legacy.mkdir(mode=0o700)
        before = snapshot(legacy)
        with pytest.raises(FileExistsError):
            factory(legacy, session_id='legacy-child', resumed=False)
        with pytest.raises((ValueError, OSError)):
            factory(legacy, session_id='legacy-child', resumed=True)
        assert snapshot(legacy) == before
        absent = root.directory.parent / 'absent-child'
        with pytest.raises((ValueError, OSError)):
            factory(absent, session_id='absent-child', resumed=True)
        assert not absent.exists()


@pytest.mark.parametrize('kind', ['foreign-parent', 'foreign-id', 'relative', 'traversal'])
def test_child_factory_refuses_foreign_path_and_identity_before_creation(tmp_path, kind):
    selected = SourceBootstrap.acquire(tmp_path / 'workspace', 'root', admit=StartGuard())
    with closing(selected.binding) as root:
        parent = root.directory.parent
        path = {'foreign-parent': tmp_path / 'child',
                'foreign-id': parent / 'not-child',
                'relative': Path('child'),
                'traversal': parent / 'nested' / '..' / 'child'}[kind]
        before = snapshot(parent)
        with pytest.raises(ValueError, match='workspace and session ID'):
            selected.child_factory(path, session_id='child', resumed=False)
        assert snapshot(parent) == before


def test_child_resume_requires_own_enrollment_not_just_parent_custody(tmp_path):
    selected = SourceBootstrap.acquire(tmp_path / 'workspace', 'root', admit=StartGuard())
    with closing(selected.binding) as root:
        path = root.directory.parent / 'child'
        selected.child_factory(path, session_id='child', resumed=False).close()
        (path / '.versioned-enrollment.json').unlink()
        before = snapshot(path)
        with pytest.raises((ValueError, OSError)):
            selected.child_factory(path, session_id='child', resumed=True)
        assert snapshot(path) == before


def test_closed_root_factory_cannot_allocate_and_fresh_root_can_reacquire(tmp_path):
    workspace = tmp_path / 'workspace'
    first = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    expected, root = first.binding.expected, first.binding.directory
    first.binding.close()
    child = root.parent / 'child'
    with pytest.raises(VersionedSessionConflict, match='closed'):
        first.child_factory(child, session_id='child', resumed=False)
    assert not child.exists()
    fresh = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    with closing(fresh.binding) as owner:
        assert fresh.resumed is True and owner.expected == expected
        with closing(fresh.child_factory(child, session_id='child', resumed=False)) as binding:
            assert binding.load_messages() == []


def test_live_stop_before_acquisition_does_not_create_any_parent(tmp_path):
    workspace = tmp_path / 'workspace'
    guard = StartGuard()
    guard.stopped = True
    with pytest.raises(RuntimeError, match='stopped'):
        SourceBootstrap.acquire(workspace, 'root', admit=guard)
    assert not sessions_dir(workspace).exists()
    with pytest.raises(TypeError, match='live application'):
        SourceBootstrap.acquire(workspace, 'root', admit=True)


def test_live_stop_after_publication_releases_custody_without_replay(tmp_path):
    workspace = tmp_path / 'workspace'
    path = sessions_dir(workspace) / 'root'
    guard = StartGuard()

    def admit():
        if path.exists():
            guard.stopped = True
        guard()

    with pytest.raises(RuntimeError, match='stopped'):
        SourceBootstrap.acquire(workspace, 'root', admit=admit)
    assert path.is_dir() and not list(path.parent.glob('.versioned-new-*'))
    selected = SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    with closing(selected.binding) as binding:
        assert selected.resumed is True and binding.expected.message_count == 0


def test_child_factory_rechecks_live_stop(tmp_path):
    guard = StartGuard()
    selected = SourceBootstrap.acquire(tmp_path / 'workspace', 'root', admit=guard)
    with closing(selected.binding) as root:
        guard.stopped = True
        child = root.directory.parent / 'child'
        with pytest.raises(RuntimeError, match='stopped'):
            selected.child_factory(child, session_id='child', resumed=False)
        assert not child.exists()
        root.check()


def test_existing_nonprivate_parent_is_refused_not_chmodded(tmp_path):
    workspace = tmp_path / 'workspace'
    parent = sessions_dir(workspace)
    parent.mkdir(parents=True, mode=0o755)
    before = snapshot(parent)
    with pytest.raises(VersionedSessionConflict, match='private and owned'):
        SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert snapshot(parent) == before


def test_linked_root_or_missing_parent_below_link_never_mutates_target(tmp_path):
    workspace = tmp_path / 'workspace'
    parent = sessions_dir(workspace)
    parent.mkdir(parents=True, mode=0o700)
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    (parent / 'root').symlink_to(target, target_is_directory=True)
    before = snapshot(target)
    with pytest.raises(ValueError, match='real directory'):
        SourceBootstrap.acquire(workspace, 'root', admit=StartGuard())
    assert snapshot(target) == before
    # A linked projects component must not be followed to create new parents.
    other = tmp_path / 'other-workspace'
    project = sessions_dir(other).parent
    project.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        SourceBootstrap.acquire(other, 'root', admit=StartGuard())
    assert snapshot(target) == before
