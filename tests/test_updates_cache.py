"""Real Git checkouts: generated cache differences are not source edits."""
import importlib._bootstrap_external
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from amplifier_web.host.config import _import_registry
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager, active_release, cache_changes, foundation_home


def git(path, *args):
    return subprocess.check_output(['git', *map(str, args)], cwd=path, stderr=subprocess.DEVNULL).decode().strip()


def pyc(source):
    return importlib._bootstrap_external._code_to_timestamp_pyc(compile(source, 'module.py', 'exec'), 0, len(source))


@pytest.fixture
def repository(tmp_path):
    root = tmp_path/'repository'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.email', 'cache-test@example.invalid')
    git(root, 'config', 'user.name', 'Cache fixture')
    (root/'bundle.py').write_text('value = 1\n')
    (root/'CLAUDE.md').write_text('Original instructions\n')
    (root/'AGENTS.md').symlink_to('CLAUDE.md')
    (root/'__pycache__').mkdir()
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 1'))
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Original')
    return root


def cache_artifacts(root):
    (root/'__pycache__/module.cpython-313.pyc').write_bytes(pyc('value = 2'))
    (root/'AGENTS.md').unlink()
    (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())


class Runtime:
    async def close(self):pass


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path/'no-import'))
    app = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    app.update_manager = UpdateManager(app)
    yield app
    await app.close()


async def test_only_verified_existing_cache_artifacts_are_eligible(repository, service):
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    protected, artifacts = await cache_changes(cache)
    assert protected == []
    assert set(artifacts) == {'AGENTS.md', '__pycache__/module.cpython-313.pyc'}
    rows = await service.update_manager.inventory_sources()
    assert rows[0]['eligible'] and rows[0]['status'] == 'not_checked'
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())
    (cache/'bundle.py').write_text('real source change\n')
    row = (await service.update_manager.inventory_sources())[0]
    assert not row['eligible'] and row['status'] == 'local_changes'


@pytest.mark.parametrize('change', ['source', 'staged-bytecode', 'deleted-bytecode', 'invalid-bytecode', 'bytecode-mode', 'modified-link', 'link-mode', 'deleted-link', 'changed-link', 'changed-target', 'staged-target', 'escaping-link', 'absolute-link', 'target-chain'])
async def test_real_edits_and_unproven_links_stay_protected(repository, tmp_path, change):
    root = repository
    cache_artifacts(root)
    if change == 'source':(root/'bundle.py').write_text('user = "source edit"\n')
    elif change == 'staged-bytecode':git(root, 'add', '__pycache__/module.cpython-313.pyc')
    elif change == 'deleted-bytecode':(root/'__pycache__/module.cpython-313.pyc').unlink()
    elif change == 'invalid-bytecode':(root/'__pycache__/module.cpython-313.pyc').write_bytes(b'User-authored data, not Python bytecode')
    elif change == 'bytecode-mode':(root/'__pycache__/module.cpython-313.pyc').chmod(0o755)
    elif change == 'modified-link':(root/'AGENTS.md').write_text('User-authored instructions\n')
    elif change == 'link-mode':(root/'AGENTS.md').chmod(0o755)
    elif change == 'deleted-link':(root/'AGENTS.md').unlink()
    elif change == 'changed-link':
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('bundle.py')
    elif change in {'changed-target', 'staged-target'}:
        (root/'CLAUDE.md').write_text('New user-authored instructions\n')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
        if change == 'staged-target':git(root, 'add', 'CLAUDE.md')
    elif change in {'escaping-link', 'absolute-link'}:
        (tmp_path/'outside.md').write_text('Outside the repository\n')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').symlink_to('../outside.md' if change == 'escaping-link' else tmp_path/'outside.md')
        git(root, 'add', 'AGENTS.md');git(root, 'commit', '-m', 'Tracked external link')
        (root/'AGENTS.md').unlink();(root/'AGENTS.md').write_bytes((tmp_path/'outside.md').read_bytes())
    elif change == 'target-chain':
        (root/'CLAUDE.md').unlink();(root/'CLAUDE.md').symlink_to('bundle.py')
        git(root, 'add', 'CLAUDE.md');git(root, 'commit', '-m', 'Tracked target chain')
        (root/'AGENTS.md').write_bytes((root/'CLAUDE.md').read_bytes())
    protected, _ = await cache_changes(root)
    assert protected, change


async def test_nul_paths_and_renames_cannot_be_misclassified(repository):
    name = ' instructions\twith\nwhitespace.md '
    (repository/name).symlink_to('CLAUDE.md')
    git(repository, 'add', '.');git(repository, 'commit', '-m', 'Unusual filename')
    (repository/name).unlink();(repository/name).write_bytes((repository/'CLAUDE.md').read_bytes())
    assert await cache_changes(repository) == ([], [name])
    git(repository, 'mv', 'bundle.py', ' renamed.py ')
    protected, artifacts = await cache_changes(repository)
    assert protected and artifacts == [name]


async def test_staging_normalizes_only_verified_artifacts_and_preserves_live_cache(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    (repository/'bundle.py').write_text('value = 3\n')
    (repository/'CLAUDE.md').write_text('New upstream instructions\n')
    git(repository, 'commit', '-am', 'Updated')
    new = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    git(cache, 'checkout', '--detach', old)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo', 'ref':'main'}))
    cache_artifacts(cache)
    # Do not remove arbitrary untracked files, including files named *.pyc.
    (cache/'__pycache__/personal-notes.pyc').write_text('keep my notes')
    before = (cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes()
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':str(repository), 'label':'Fixture', 'current':old, 'latest':new, 'ref':'main', 'eligible':True, 'status':'update'}]
    async def validate(stage, release):
        staged = stage/'foundation/cache/example'
        assert git(staged, 'rev-parse', 'HEAD') == new
        assert (staged/'AGENTS.md').is_symlink()
        assert (staged/'AGENTS.md').read_text() == 'New upstream instructions\n'
        assert (staged/'__pycache__/module.cpython-313.pyc').read_bytes() == pyc('value = 1')
        assert (staged/'__pycache__/personal-notes.pyc').read_text() == 'keep my notes'
    manager.validate = validate
    await manager.install()
    assert service.state['updates']['phase'] == 'installed'
    assert foundation_home(service.data_dir) != service.data_dir/'foundation'
    assert git(cache, 'rev-parse', 'HEAD') == old
    assert not (cache/'AGENTS.md').is_symlink()
    assert before == ((cache/'AGENTS.md').read_bytes(), (cache/'__pycache__/module.cpython-313.pyc').read_bytes())


async def test_source_edit_added_after_check_blocks_staging(repository, service):
    old = git(repository, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/example'
    shutil.copytree(repository, cache, symlinks=True)
    cache_artifacts(cache)
    assert (await cache_changes(cache))[0] == []
    (cache/'bundle.py').write_text('a later real source edit\n')
    manager = service.update_manager
    manager.inventory = [{'id':'repo', 'path':'cache/example', 'url':str(repository), 'label':'Fixture', 'current':old, 'latest':old, 'ref':'main', 'eligible':True, 'status':'update'}]
    await manager.install()
    assert service.state['updates']['phase'] == 'error'
    assert not active_release(service.data_dir)
    assert (cache/'bundle.py').read_text() == 'a later real source edit\n'
    assert not (cache/'AGENTS.md').is_symlink()


def test_import_preserves_symlinks_including_external_links(tmp_path, repository):
    legacy = tmp_path/'legacy'
    shutil.copytree(repository, legacy/'cache/repository', symlinks=True)
    outside = tmp_path/'external';outside.mkdir();(outside/'notes.txt').write_text('private')
    (legacy/'cache/repository/external').symlink_to(outside, target_is_directory=True)
    home = tmp_path/'imported'
    _import_registry(home, legacy)
    imported = home/'foundation/cache/repository'
    assert (imported/'AGENTS.md').is_symlink()
    assert (imported/'AGENTS.md').readlink() == Path('CLAUDE.md')
    assert (imported/'external').is_symlink()
    assert git(imported, 'status', '--porcelain', '--untracked-files=no') == ''


# Reviewed upstream hook at cd855a4f21e55a0e58227637c1505e1a695b75e4.
# Stored as inert text: tests never import or execute repository build hooks.
WIKI_HEADER = b'''"""Single source of truth for the wiki-weaver package version.

Kept as a leaf module with no imports so that any submodule can safely
import __version__ without risk of triggering a circular import through
the wiki_weaver package __init__.

This value is baked in at wheel-build time by the git-version hatchling
build hook (see hatch_build.py at the repo root) -- it is the commit date
+ short SHA of the exact commit this wheel was built from, not the build
date. Do not hand-edit; it is overwritten on the next build.
"""

'''
WIKI_VERSION = 'wiki_weaver/_version.py'
WIKI_PROJECT = '''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "wiki-weaver"
[tool.hatch.build.hooks.custom]
path = "hatch_build.py"
'''


def wiki_generated(root):
    date = git(root, 'log', '-1', '--format=%cd', '--date=format:%Y.%m.%d')
    sha = git(root, 'rev-parse', '--short', 'HEAD')
    return WIKI_HEADER + f'__version__ = "{date}-{sha}"\n'.encode()


@pytest.fixture
def wiki_repository(repository):
    hook = (Path(__file__).parent/'fixtures/wiki_weaver_hatch_build.txt').read_bytes()
    assert hashlib.sha256(hook).hexdigest() == 'd0733aa7fecaadf307fdeb42dd202c4fec1a8255e56b24bb23777cbd003620f3'
    (repository/'hatch_build.py').write_bytes(hook)
    (repository/'pyproject.toml').write_text(WIKI_PROJECT)
    (repository/'wiki_weaver').mkdir()
    (repository/WIKI_VERSION).write_bytes(WIKI_HEADER + b'__version__ = "2026.07.08-18df48b"\n')
    git(repository, 'add', '.')
    git(repository, 'commit', '-m', 'Wiki version hook fixture')
    (repository/WIKI_VERSION).write_bytes(wiki_generated(repository))
    return repository


@pytest.mark.parametrize('abbrev', [4, 7, 12, 40, 'auto'])
async def test_wiki_generated_version_is_eligible_without_mutation(wiki_repository, service, abbrev):
    root = wiki_repository
    git(root, 'config', 'core.abbrev', abbrev)
    (root/WIKI_VERSION).write_bytes(wiki_generated(root))
    cache = service.data_dir/'foundation/cache/wiki'
    shutil.copytree(root, cache, symlinks=True)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/wiki', 'ref':'main'}))
    before = (cache/WIKI_VERSION).read_bytes()
    index = (cache/'.git/index').read_bytes()
    assert await cache_changes(cache) == ([], [WIKI_VERSION])
    row = (await service.update_manager.inventory_sources())[0]
    assert row['eligible'] and row['status'] == 'not_checked'
    assert (cache/WIKI_VERSION).read_bytes() == before
    assert (cache/'.git/index').read_bytes() == index


@pytest.mark.parametrize('change', [
    'wrong-sha', 'wrong-date', 'extra-assignment', 'comment', 'docstring',
    'crlf', 'no-final-newline', 'bom', 'staged-version', 'staged-then-generated',
    'version-mode', 'version-symlink', 'symlink-parent', 'internal-symlink-parent',
    'head-version-symlink', 'deleted-version',
    'missing-hook', 'changed-hook', 'staged-hook', 'hook-mode', 'hook-symlink',
    'unknown-hook', 'staged-hook-restored', 'staged-hook-mode',
    'modified-project', 'staged-project', 'staged-project-restored', 'staged-project-mode', 'project-mode',
    'project-symlink', 'wrong-project', 'wrong-backend', 'wrong-hook-path',
    'no-custom-hook', 'backend-path', 'wrong-build-requires', 'invalid-toml', 'invalid-project-shape',
    'head-extra-assignment', 'head-docstring', 'different-version-path',
])
async def test_unproven_wiki_versions_stay_protected(wiki_repository, tmp_path, change):
    root = wiki_repository
    version = root/WIKI_VERSION
    hook = root/'hatch_build.py'
    project = root/'pyproject.toml'
    value = version.read_bytes()
    if change == 'wrong-sha':
        version.write_bytes(value.replace(git(root, 'rev-parse', '--short', 'HEAD').encode(), b'0000000'))
    elif change == 'wrong-date':
        version.write_bytes(value.replace(git(root, 'log', '-1', '--format=%cd', '--date=format:%Y.%m.%d').encode(), b'2000.01.01'))
    elif change == 'extra-assignment':version.write_bytes(value + b'other = 1\n')
    elif change == 'comment':version.write_bytes(value + b'# personal note\n')
    elif change == 'docstring':version.write_bytes(value.replace(b'Single source', b'Manual source'))
    elif change == 'crlf':version.write_bytes(value.replace(b'\n', b'\r\n'))
    elif change == 'no-final-newline':version.write_bytes(value[:-1])
    elif change == 'bom':version.write_bytes(b'\xef\xbb\xbf' + value)
    elif change == 'staged-version':git(root, 'add', WIKI_VERSION)
    elif change == 'staged-then-generated':
        version.write_bytes(value + b'# staged note\n');git(root, 'add', WIKI_VERSION);version.write_bytes(value)
    elif change == 'version-mode':version.chmod(0o755)
    elif change == 'deleted-version':version.unlink()
    elif change == 'symlink-parent':
        (root/'wiki_weaver').rename(tmp_path/'external-wiki')
        (root/'wiki_weaver').symlink_to(tmp_path/'external-wiki', target_is_directory=True)
    elif change == 'internal-symlink-parent':
        (root/'wiki_weaver').rename(root/'other-wiki')
        (root/'wiki_weaver').symlink_to('other-wiki', target_is_directory=True)
    elif change == 'head-version-symlink':
        (root/'version-target.py').write_bytes(value)
        version.unlink();version.symlink_to('../version-target.py')
        git(root, 'add', '.');git(root, 'commit', '-m', 'Tracked version symlink')
        version.unlink();version.write_bytes(value)
    elif change in {'version-symlink', 'hook-symlink', 'project-symlink'}:
        path = {'version-symlink':version, 'hook-symlink':hook, 'project-symlink':project}[change]
        target = tmp_path/'external-file';target.write_bytes(path.read_bytes())
        path.unlink();path.symlink_to(target)
    elif change == 'missing-hook':hook.unlink()
    elif change in {'changed-hook', 'staged-hook', 'unknown-hook'}:
        hook.write_bytes(hook.read_bytes() + b'\n# different hook\n')
        if change == 'staged-hook':git(root, 'add', 'hatch_build.py')
        if change == 'unknown-hook':git(root, 'commit', '-am', 'Unknown hook')
    elif change == 'hook-mode':hook.chmod(0o755)
    elif change in {'staged-hook-restored', 'staged-project-restored'}:
        path = hook if change == 'staged-hook-restored' else project
        original = path.read_bytes()
        path.write_bytes(original + b'\n# staged edit\n')
        git(root, 'add', path.name);path.write_bytes(original)
    elif change in {'staged-hook-mode', 'staged-project-mode'}:
        path = hook if change == 'staged-hook-mode' else project
        git(root, 'update-index', '--chmod=+x', path.name)
    elif change in {'modified-project', 'staged-project'}:
        project.write_text(WIKI_PROJECT + '# personal edit\n')
        if change == 'staged-project':git(root, 'add', 'pyproject.toml')
    elif change == 'project-mode':project.chmod(0o755)
    elif change in {'head-extra-assignment', 'head-docstring'}:
        version.write_bytes(value + b'other = 1\n' if change == 'head-extra-assignment' else value.replace(b'Single source', b'Manual source'))
        git(root, 'commit', '-am', 'Different original version')
    elif change == 'different-version-path':
        git(root, 'mv', WIKI_VERSION, 'wiki_weaver/version.py')
        git(root, 'commit', '-am', 'Other path')
        version = root/'wiki_weaver/version.py'
    else:
        replacements = {
            'wrong-project': WIKI_PROJECT.replace('wiki-weaver', 'another-package'),
            'wrong-backend': WIKI_PROJECT.replace('hatchling.build', 'other.build'),
            'wrong-hook-path': WIKI_PROJECT.replace('hatch_build.py', 'other.py'),
            'no-custom-hook': WIKI_PROJECT.split('[tool.hatch')[0],
            'backend-path': WIKI_PROJECT.replace('[build-system]', '[build-system]\nbackend-path = ["."]'),
            'wrong-build-requires': WIKI_PROJECT.replace('["hatchling"]', '["other"]'),
            'invalid-toml': 'not toml',
            'invalid-project-shape': 'project = "wiki-weaver"\n',
        }
        project.write_text(replacements[change])
        git(root, 'commit', '-am', 'Unrecognized build configuration')
    if change in {'unknown-hook', 'head-extra-assignment', 'head-docstring', 'different-version-path',
                  'wrong-project', 'wrong-backend', 'wrong-hook-path', 'no-custom-hook',
                  'backend-path', 'wrong-build-requires', 'invalid-toml', 'invalid-project-shape'}:
        version.write_bytes(wiki_generated(root))
    protected, artifacts = await cache_changes(root)
    assert str(version.relative_to(root)) in protected
    assert str(version.relative_to(root)) not in artifacts


@pytest.mark.parametrize(('command', 'output'), [
    ('log', b'2026.08.31\ninjected\n'),
    ('log', b'2026.8.31\n'),
    ('log', b''),
    ('rev-parse', b'abc\n'),
    ('rev-parse', b'a'*65 + b'\n'),
    ('rev-parse', b'abc1234\ninjected\n'),
    ('rev-parse', b'not-hex\n'),
    ('rev-parse', None),
])
async def test_wiki_git_outputs_are_bounded_and_fail_closed(wiki_repository, monkeypatch, command, output):
    from amplifier_web import updates
    real_process = updates.process
    async def process(*args, **kwargs):
        if args[:2] == ('git', command) and (command != 'rev-parse' or args[2] == '--short'):
            if output is None:raise RuntimeError('Git failed')
            return output
        return await real_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    assert await cache_changes(wiki_repository) == ([WIKI_VERSION], [])


@pytest.mark.parametrize('same_date', [False, True])
@pytest.mark.parametrize('payload', ['mixed', 'captured'])
async def test_wiki_head_moving_after_date_is_protected(wiki_repository, monkeypatch, same_date, payload):
    from amplifier_web import updates
    root = wiki_repository
    tree = git(root, 'rev-parse', 'HEAD^{tree}')
    commits = []
    for label, date in [('A', '2026-08-30T12:00:00+0000'),
                        ('B', '2026-08-30T12:00:00+0000' if same_date else '2026-08-31T12:00:00+0000')]:
        monkeypatch.setenv('GIT_AUTHOR_DATE', date)
        monkeypatch.setenv('GIT_COMMITTER_DATE', date)
        commits.append(git(root, 'commit-tree', tree, '-m', label))
    first, second = commits
    assert first != second
    git(root, 'update-ref', 'HEAD', first)
    date = git(root, 'log', '-1', '--format=%cd', '--date=format:%Y.%m.%d', first)
    sha = git(root, 'rev-parse', '--short', second if payload == 'mixed' else first)
    (root/WIKI_VERSION).write_bytes(WIKI_HEADER + f'__version__ = "{date}-{sha}"\n'.encode())
    before = (root/WIKI_VERSION).read_bytes()
    real_process = updates.process
    calls = []
    async def process(*args, **kwargs):
        calls.append(args)
        result = await real_process(*args, **kwargs)
        if args[:2] == ('git', 'log'):
            git(root, 'update-ref', 'HEAD', second)
        return result
    monkeypatch.setattr(updates, 'process', process)
    assert await cache_changes(root) == ([WIKI_VERSION], [])
    assert git(root, 'rev-parse', 'HEAD') == second
    assert (root/WIKI_VERSION).read_bytes() == before
    for name in (WIKI_VERSION, 'hatch_build.py', 'pyproject.toml'):
        assert ('git', '--literal-pathspecs', 'ls-tree', '-z', first, '--', name) in calls
    assert ('git', 'log', '-1', '--format=%cd', '--date=format:%Y.%m.%d', first) in calls
    assert ('git', 'rev-parse', '--short', first) in calls
    if payload == 'captured':
        assert calls.count(('git', 'rev-parse', '--verify', 'HEAD^{commit}')) == 2


@pytest.mark.parametrize('output', [b'', b'a'*39+b'\n', b'a'*41+b'\n', b'a'*63+b'\n',
                                   b'a'*65+b'\n', b'g'*40+b'\n', b'a'*40+b'\ninjected\n'])
async def test_wiki_malformed_captured_head_is_protected(wiki_repository, monkeypatch, output):
    from amplifier_web import updates
    real_process = updates.process
    async def process(*args, **kwargs):
        if args == ('git', 'rev-parse', '--verify', 'HEAD^{commit}'):return output
        return await real_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    assert await cache_changes(wiki_repository) == ([WIKI_VERSION], [])


async def test_wiki_unrelated_abbreviation_is_protected(wiki_repository, monkeypatch):
    from amplifier_web import updates
    root = wiki_repository
    head = git(root, 'rev-parse', 'HEAD')
    unrelated = ('0' if head[0] != '0' else '1') + head[1:7]
    date = git(root, 'log', '-1', '--format=%cd', '--date=format:%Y.%m.%d')
    (root/WIKI_VERSION).write_bytes(WIKI_HEADER + f'__version__ = "{date}-{unrelated}"\n'.encode())
    real_process = updates.process
    async def process(*args, **kwargs):
        if args[:3] == ('git', 'rev-parse', '--short'):return unrelated.encode() + b'\n'
        return await real_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    assert await cache_changes(root) == ([WIKI_VERSION], [])


@pytest.mark.parametrize('late_edit', [False, True])
async def test_wiki_staging_rechecks_and_restores_only_the_copy(wiki_repository, service, late_edit):
    root = wiki_repository
    old = git(root, 'rev-parse', 'HEAD')
    original = subprocess.check_output(['git', 'show', f'HEAD:{WIKI_VERSION}'], cwd=root)
    (root/'bundle.py').write_text('new upstream source\n')
    # Do not commit the build artifact.
    git(root, 'add', 'bundle.py');git(root, 'commit', '-m', 'Next upstream')
    new = git(root, 'rev-parse', 'HEAD')
    cache = service.data_dir/'foundation/cache/wiki'
    shutil.copytree(root, cache, symlinks=True)
    git(cache, 'restore', WIKI_VERSION)
    git(cache, 'checkout', '--detach', old)
    (cache/WIKI_VERSION).write_bytes(wiki_generated(cache))
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/wiki', 'ref':'main'}))
    manager = service.update_manager
    row = (await manager.inventory_sources())[0]
    assert row['eligible']
    manager.inventory = [{**row, 'url':str(root), 'latest':new, 'status':'update'}]
    if late_edit:
        (cache/WIKI_VERSION).write_bytes((cache/WIKI_VERSION).read_bytes() + b'# later user edit\n')
    before = (cache/WIKI_VERSION).read_bytes()
    async def validate(stage, release):
        assert not late_edit, 'User edits must block before validation'
        staged = stage/'foundation/cache/wiki'
        assert git(staged, 'rev-parse', 'HEAD') == new
        assert (staged/WIKI_VERSION).read_bytes() == original
        assert git(staged, 'status', '--porcelain', '--untracked-files=no') == ''
    manager.validate = validate  # No runtime preparation or model calls.
    await manager.install()
    assert service.state['updates']['phase'] == ('error' if late_edit else 'installed')
    assert bool(active_release(service.data_dir)) is not late_edit
    assert git(cache, 'rev-parse', 'HEAD') == old
    assert (cache/WIKI_VERSION).read_bytes() == before
