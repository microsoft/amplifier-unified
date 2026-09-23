"""Stamp probes preserve discovery and path semantics without reading bodies."""
import os
from pathlib import Path

import pytest

from amplifier_web.native_history import NativeHistory


FILES = ('metadata.json', 'metadata.json.backup', 'transcript.jsonl',
         'transcript.jsonl.backup', 'naming.json', 'context-intelligence/metadata.json')


@pytest.mark.parametrize('relative', [False, True])
@pytest.mark.parametrize('parent_symlink', [False, True])
def test_stamp_paths_keep_basename_order_spelling_and_all_missing_probes(tmp_path, monkeypatch, relative, parent_symlink):
    monkeypatch.chdir(tmp_path)
    project = Path('project with spaces') if relative else tmp_path / 'project with spaces'
    sessions = project / 'sessions'
    sessions.mkdir(parents=True)
    names = ['zeta', 'alpha', 'A', 'child:run_1', 'with spaces', 'ü-name']
    for name in names + ['.hidden']:
        (sessions / name).mkdir()
    (sessions / 'not-directory').write_text('ignored')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (sessions / 'linked-session').symlink_to(outside, target_is_directory=True)
    if parent_symlink:
        actual = project / 'actual-sessions'
        sessions.rename(actual)
        sessions.symlink_to(actual.resolve(), target_is_directory=True)
    # _directories still returns Paths for metadata-reading/discovery callers.
    assert NativeHistory._directories(sessions) == [sessions / name for name in sorted(names)]
    history = NativeHistory(tmp_path / 'home')
    stamp = history._project_stamp(project, {})
    expected_paths = [str(project / 'metadata.json'), str(sessions)]
    expected_paths.extend(str(sessions / name / filename) for name in sorted(names) for filename in FILES)
    assert stamp[:2] == ((), ())
    assert [row[0] for row in stamp[2:]] == expected_paths
    assert all(type(row[0]) is str for row in stamp[2:])
    assert stamp[2] == (str(project / 'metadata.json'), None)
    assert all(row == (path, None) for row, path in zip(stamp[4:], expected_paths[2:]))


def test_stamp_preserves_regular_symlink_and_missing_file_signatures(tmp_path):
    project = tmp_path / 'project'
    root = project / 'sessions/root'
    root.mkdir(parents=True)
    metadata = root / 'metadata.json'
    metadata.write_text('{}')
    target = tmp_path / 'outside.json'
    target.write_text('{}')
    (root / 'naming.json').symlink_to(target)
    paths = [project / 'metadata.json', project / 'sessions',
             *(root / name for name in FILES)]
    expected = [(), ()]
    for path in paths:
        try:
            info = os.stat(path, follow_symlinks=False)
            expected.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                             info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            expected.append((str(path), None))
    assert NativeHistory(tmp_path / 'home')._project_stamp(project, {}) == tuple(expected)


def test_missing_sessions_directory_still_has_absent_stamp(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    assert NativeHistory(tmp_path / 'home')._project_stamp(project, {}) == (
        (), (), (str(project / 'metadata.json'), None), (str(project / 'sessions'), None))


def test_directory_enumeration_errors_retain_prior_rows_and_report_issue(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    from amplifier_web.session_files import project_slug
    root = home / 'projects' / project_slug(workspace) / 'sessions/root'
    root.mkdir(parents=True)
    import json
    (root / 'metadata.json').write_text(json.dumps({'working_dir': str(workspace), 'name': 'Retained'}))
    (root / 'transcript.jsonl').write_text('body must not be read')
    history = NativeHistory(home)
    first = history.scan()
    history.scan()  # Establish all learned workspace stamp inputs.
    original = os.scandir
    def denied(path):
        if Path(path) == root.parent:
            raise PermissionError('synthetic enumeration denied')
        return original(path)
    monkeypatch.setattr(os, 'scandir', denied)
    with pytest.raises(PermissionError, match='synthetic enumeration denied'):
        history._project_stamp(root.parent.parent, {})
    result = history.scan()
    assert result['sessions'] == first['sessions']
    assert {'kind': 'unreadable', 'nativeProject': root.parent.parent.name} in result['issues']


def test_metadata_stat_error_is_not_converted_to_a_missing_probe(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    root = project / 'sessions/root'
    root.mkdir(parents=True)
    metadata = root / 'metadata.json'
    metadata.write_text('{}')
    original = os.stat
    def denied(path, *args, **kwargs):
        if Path(path) == metadata:
            raise PermissionError('synthetic metadata stat denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', denied)
    with pytest.raises(PermissionError, match='synthetic metadata stat denied'):
        NativeHistory(tmp_path / 'home')._project_stamp(project, {})
