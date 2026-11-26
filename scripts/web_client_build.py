"""Assemble the selected independent client using offline, verified inputs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontend_build_artifact as output
import web_client_source as delivery
from browser_check_runner import compose_browser_plan

command = delivery.command


def read(path):
    return json.loads(path.read_text())


def member(root, name):
    relative = PurePosixPath(name)
    path = root / relative
    if (not name or relative.is_absolute() or '..' in relative.parts
            or not path.resolve().is_relative_to(root.resolve())
            or not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError(f'Expected a regular file inside its source: {name}')
    return path


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if stat.S_ISDIR(path.lstat().st_mode):
            continue
        name = path.relative_to(directory).as_posix()
        if name == 'client-component.json':
            continue  # The fixed self-description cannot include its own hash.
        content = member(directory, name).read_bytes()
        result[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    return result


def replace_directory(ready, destination):
    backup = Path(tempfile.mkdtemp(prefix='.web-client-previous-', dir=destination.parent))
    previous, retain = backup / 'source', False
    try:
        if destination.exists():
            destination.rename(previous)
        try:
            ready.rename(destination)
        except BaseException:
            if previous.exists():
                try:
                    previous.rename(destination)
                except OSError as error:
                    retain = True
                    raise ValueError(f'Restore previous client output from {previous}') from error
            raise
    finally:
        if not retain:
            shutil.rmtree(backup)


def invoke(source, module, expression, *args, node='node'):
    result = command(node, '--input-type=module', '-e',
                     f'const m=await import(process.argv[1]); {expression}',
                     (source / 'scripts' / module).as_uri(), str(source), *args,
                     capture=True, timeout=30)
    return result.stdout


def verify_source(source, record, *, node='node'):
    command(node, source / 'scripts/client-source.mjs', 'verify', record,
            capture=True, timeout=30)


def verify_inputs(source, *, node='node'):
    invoke(source, 'prepare-inputs.mjs',
           'm.verifyInputs(process.argv[2],process.argv[3]);',
           source / '.build-inputs', node=node)


def prepare_inputs(root, source, *, node='node'):
    # Validate the pair before replacing a prepared client or installing dependencies.
    compose_browser_plan(read(root / 'tests/web/browser-checks.json'),
                         read(source / 'tests/browser-checks.json'))
    lock_path = source / 'build-inputs.lock.json'
    lock = read(lock_path)
    # Copy only the selected shared files. The client owns lock validation and
    # installation; an unrelated host version never supplies client identity.
    with tempfile.TemporaryDirectory(prefix='.web-client-inputs-', dir=source.parent) as name:
        export = Path(name)
        for name, expected in lock['inputs'].items():
            content = member(root, name).read_bytes()
            if {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)} != expected:
                raise ValueError(f'Shared client input changed: {name}; refresh its canonical selection')
            target = export / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        shutil.copyfile(lock_path, export / lock_path.name)
        if (source / '.build-inputs').exists():
            verify_inputs(source, node=node)
        else:
            command(node, source / 'scripts/prepare-inputs.mjs', export, capture=True, timeout=30)


def prepare(root=ROOT, *, node='node'):
    root = root.resolve()
    source = root / '.build/web-client'
    source.parent.mkdir(parents=True, exist_ok=True)
    recovery = sorted(source.parent.glob('.web-client-previous-*'))
    if recovery:
        raise ValueError(f'Previous client recovery retained at {recovery[0]}; inspect it before preparing again')
    selected = root / 'components/web-client.source.json'
    record = delivery.selection(selected)
    if source.exists() or source.is_symlink():
        if source.is_symlink():
            raise ValueError('Prepared client must be an owned directory, not a link')
        previous = source / '.client-source.json'
        # Validate the old selection before replacing it; never erase an edit.
        verify_source(source, previous, node=node)
        delivery.verify_development(source)
        if read(previous) == record:
            prepare_inputs(root, source, node=node)
            return source
    with tempfile.TemporaryDirectory(prefix='.web-client-prepare-', dir=source.parent) as name:
        stage = Path(name)
        ready = stage / 'ready'
        delivery.prepare(root, selected, ready, node=node, development=True)
        prepare_inputs(root, ready, node=node)
        replace_directory(ready, source)
    return source


def verify_artifact(root, source, component, static, *, node='node'):
    record = delivery.selection(root / 'components/web-client.source.json')
    if read(source / '.client-source.json') != record:
        raise ValueError('Prepared client selection differs from the distribution')
    verify_source(source, source / '.client-source.json', node=node)
    delivery.verify_development(source)
    verify_inputs(source, node=node)
    lock_path = source / 'build-inputs.lock.json'
    lock = read(lock_path)
    identity = json.loads(invoke(source, 'build-identity.mjs',
                                'console.log(JSON.stringify(m.buildIdentity(process.argv[2])));', node=node))
    expected = {'schema': 1, 'kind': 'unified-web-static-artifact', 'source': record['source'],
                'sharedInputs': {'source': lock['source'],
                                 'lockSha256': hashlib.sha256(lock_path.read_bytes()).hexdigest()},
                'pairedServer': {**lock['source'], 'transport': 'current-http-sse',
                                 'qualification': 'unqualified'},
                'build': identity, 'assets': inventory(static)}
    if component != expected or read(static / 'build.json') != identity:
        raise ValueError('Installed client assets or provenance differ from the selected build')
    # Current catalogs/SDK/branding must still match, without preparing or writing.
    for name, digest in lock['inputs'].items():
        content = member(root, name).read_bytes()
        if {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)} != digest:
            raise ValueError(f'Shared client input changed: {name}')
    return component


def clear_staging(work):
    # Keep state.json until the last disposable staging directory is gone.
    for name in ('package', 'new'):
        path = work / name
        if path.exists():
            shutil.rmtree(path)
    output.discard_verified(work)


def recover_static(root, work):
    if not work.exists():
        return
    record = work / 'state.json'
    directories, files = {'package', 'new', 'old'}, {'state.tmp', 'state.json'}
    for path in work.iterdir():
        if path.is_symlink() or not ((path.name in files and path.is_file())
                                     or (path.name in directories and path.is_dir())):
            raise ValueError(f'Unrecognized build recovery at {work}')
    if not record.exists():
        if set(path.name for path in work.iterdir()) - {'state.tmp'}:
            raise ValueError(f'Missing build recovery record at {work}')
        clear_staging(work)
        return
    state = read(record)
    target = str(root / output.STATIC)
    if not isinstance(state, dict) or state.get('target') != target:
        raise ValueError(f'Build recovery belongs to a different output at {work}')
    if state == {'phase': 'preparing', 'target': target} and not (work / 'old').exists():
        clear_staging(work)
        return
    if (state.keys() != {'phase', 'target', 'before', 'after'}
            or state['phase'] not in {'prepared', 'verified'} or (work / 'package').exists()
            or (state['phase'] == 'verified' and (work / 'new').exists())):
        raise ValueError(f'Unrecognized build recovery record at {work}')
    if state['phase'] == 'prepared':
        output.replace_static(root, work, state)
        output.save_apply_state(work, state, 'verified')
    output.finish_apply(root, work, state)


@contextmanager
def static_build(root):
    """Build and recover at the same two rename boundaries as artifact apply."""
    root = root.resolve()
    target = root / output.STATIC
    work = root / '.web-client-output'
    if any(path.is_symlink() for path in (target.parent, target, work)):
        raise ValueError('Installed client paths must not be links')
    if (root / '.git').exists():
        git_dir = Path(output.git(root, 'rev-parse', '--absolute-git-dir').decode().strip())
        pending = git_dir / 'frontend-build-apply'
        lock = pending.with_suffix('.lock')
    else:
        cache = root / '.build'
        cache.mkdir(exist_ok=True)
        pending, lock = None, cache / 'web-client-output.lock'
    with output.apply_lock(lock):
        if pending is not None and pending.exists():
            raise ValueError(f'Pending generated artifact recovery at {pending}; rerun artifact apply')
        recover_static(root, work)
        work.mkdir()
        owner = {'target': str(target)}
        state = dict(owner)
        output.save_apply_state(work, state, 'preparing')
        try:
            yield work
            new, old = work / 'new', work / 'old'
            state.update(before=output.tree(target, bounded=False) if target.exists() else None,
                         after=output.tree(new, bounded=False))
            output.save_apply_state(work, state, 'prepared')
            if target.exists():
                target.rename(old)
            new.rename(target)
            output.save_apply_state(work, state, 'verified')
        except BaseException:
            if state.get('phase') == 'prepared':
                try:
                    output.restore_original(root, work, state)
                except OSError as error:
                    raise ValueError(f'Restore previous client output from {work / "old"}') from error
            if not (work / 'old').exists():
                output.save_apply_state(work, dict(owner), 'preparing')
                clear_staging(work)
            raise
        output.discard_verified(work)


def install(root, package):
    with static_build(root) as stage:
        shutil.copytree(package / 'static', stage / 'new')
        shutil.copyfile(package / 'component.json', stage / 'new/client-component.json')


def build(root=ROOT, *, node='node', npm='npm'):
    root = root.resolve()
    with static_build(root) as stage:
        source = prepare(root, node=node)
        if sys.platform == 'win32':
            # CreateProcess does not apply the shell's PATHEXT lookup to bare npm.
            npm = shutil.which(npm) or npm
        command(npm, 'run', 'build', '--prefix', source, timeout=300)
        package = stage / 'package'
        peer = read(source / 'build-inputs.lock.json')['source']['revision']
        command(node, source / 'scripts/package-client.mjs', package, peer,
                '--source-record', source / '.client-source.json', capture=True, timeout=60)
        component = read(package / 'component.json')
        verify_artifact(root, source, component, package / 'static', node=node)
        (package / 'static').rename(stage / 'new')
        shutil.copyfile(package / 'component.json', stage / 'new/client-component.json')
        shutil.rmtree(package)
    return component


def verify(root=ROOT, *, node='node'):
    root = root.resolve()
    return verify_artifact(root, root / '.build/web-client',
                           read(member(root / 'amplifier_web/static', 'client-component.json')),
                           root / 'amplifier_web/static', node=node)


def develop(root=ROOT, *, npm='npm'):
    """Run the selected client's own development server in the owned environment."""
    source = root.resolve() / '.build/web-client'
    if not (source / '.client-source.json').is_file():
        raise ValueError('Run scripts/web_client_build.py prepare and install its dependencies first')
    command(shutil.which(npm) or npm, 'run', 'dev', '--prefix', source, timeout=86400)
    return {'directory': str(source)}


def build_development(root=ROOT, *, npm='npm'):
    """Build deliberate local experiments without a qualifying component receipt."""
    root = root.resolve()
    with static_build(root) as stage:
        source = root / '.build/web-client'
        if not (source / '.client-source.json').is_file():
            raise ValueError('Prepare the selected client before building a development experiment')
        command(shutil.which(npm) or npm, 'run', 'build', '--prefix', source, timeout=300)
        shutil.copytree(source / 'dist', stage / 'new')
    return {'directory': str(source), 'qualification': 'development-only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'build', 'verify', 'dev', 'build-development'])
    args = parser.parse_args(argv)
    try:
        operation = {'dev': develop, 'build-development': build_development}.get(args.command)
        result = (operation or globals()[args.command])()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n{getattr(error, "stderr", "") or ""}')
    report = ({'directory': str(result)} if isinstance(result, Path) else
              {name: result[name] for name in ['source', 'build', 'pairedServer']}
              if 'build' in result else result)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
