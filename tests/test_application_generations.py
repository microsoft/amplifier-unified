import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web import application_generations as generations
from amplifier_web.app_component_graph import normalized, digest


@pytest.fixture
def qualified(tmp_path):
    home = tmp_path / 'app'
    bootstrap = tmp_path / 'tools/amplifier-unified'
    dist = bootstrap / 'lib/python3.13/site-packages/amplifier_unified-0.1.0.dist-info'
    dist.mkdir(parents=True)
    (dist / 'METADATA').write_text('Name: amplifier-unified\nVersion: 0.1.0\n')
    graph = normalized([{'name': 'amplifier-unified', 'version': '99.0.0', 'direct': None}])
    receipt = {'revision': 'a' * 40, 'generation': 'b' * 32, 'version': '99.0.0', 'componentDigest': digest(graph)}
    folder = generations.directory(home, receipt['revision'], receipt['generation'])
    folder.mkdir(parents=True)
    (folder / 'validated.json').write_text(json.dumps(receipt))
    target = generations.promote(home, receipt, bootstrap)
    return home, bootstrap, dist, target, graph


def test_qualified_launch_preserves_arguments_and_data_scope(qualified, monkeypatch):
    home, bootstrap, dist, target, graph = qualified
    monkeypatch.setattr(generations.sys, 'prefix', str(bootstrap))
    monkeypatch.setattr(generations, '__file__', str(bootstrap / 'lib/application_generations.py'))
    monkeypatch.delenv('AMPLIFIER_APP_GENERATION_EXEC', raising=False)
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(graph)))
    calls = []
    monkeypatch.setattr(generations.os, 'execv', lambda python, args: calls.append((python, args)))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    generations.delegate(['--data-dir', str(home), '--port', '8999'])
    assert len(calls) == 1
    assert calls[0][1][-2:] == ['--port', '8999']
    assert generations.os.environ['AMPLIFIER_WEB_HOME'] == str(home)
    assert generations.os.environ['AMPLIFIER_APP_GENERATION_EXEC'] == target['generation']
    monkeypatch.delenv('AMPLIFIER_APP_GENERATION_EXEC')


def test_manual_reinstall_and_development_launch_take_precedence(qualified, monkeypatch):
    home, bootstrap, dist, target, graph = qualified
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.delenv('AMPLIFIER_APP_GENERATION_EXEC', raising=False)
    calls = []
    monkeypatch.setattr(generations.os, 'execv', lambda *args: calls.append(args))
    monkeypatch.setattr(generations.sys, 'prefix', str(bootstrap))
    # A developer importing from another checkout is never redirected.
    generations.delegate([])
    assert not calls
    monkeypatch.setattr(generations, '__file__', str(bootstrap / 'lib/application_generations.py'))
    (dist / 'direct_url.json').write_text(json.dumps({'url': 'https://example.invalid/updated'}))
    generations.delegate([])
    assert not calls
    assert generations.active(home)['generation'] == target['generation']


def test_changed_qualification_receipt_is_not_a_launch_target(qualified):
    home, bootstrap, dist, target, graph = qualified
    data = json.loads((target['folder'] / 'validated.json').read_text())
    data['componentDigest'] = 'changed'
    (target['folder'] / 'validated.json').write_text(json.dumps(data))
    with pytest.raises(ValueError, match='qualification'):
        generations.active(home)


def test_bootstrap_execs_qualified_interpreter_in_real_isolated_environments(tmp_path):
    import os
    import shutil
    import subprocess
    import venv
    from amplifier_web import app_component_graph

    home = tmp_path / 'owned-app'
    bootstrap = tmp_path / 'bootstrap'
    receipt = {'revision': 'c' * 40, 'generation': 'd' * 32, 'version': '99.0.0'}
    folder = generations.directory(home, receipt['revision'], receipt['generation'])
    candidate = folder / 'tools/amplifier-unified'
    for prefix, version in ((bootstrap, '0.1.0'), (candidate, '99.0.0')):
        venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(prefix)
        site = next(prefix.glob('lib/python*/site-packages')) if os.name != 'nt' else prefix / 'Lib/site-packages'
        package = site / 'amplifier_web'
        (package / 'host').mkdir(parents=True)
        (package / '__init__.py').write_text(f'__version__ = {version!r}\n')
        (package / 'host/__init__.py').write_text('')
        (package / 'host/config.py').write_text('import os\nfrom pathlib import Path\ndef app_home(): return Path(os.environ["AMPLIFIER_WEB_HOME"])\n')
        (package / 'app_updates.py').write_text('def version_tuple(value): return tuple(int(part) for part in value.split("."))\n')
        shutil.copy2(generations.__file__, package / 'application_generations.py')
        shutil.copy2(app_component_graph.__file__, package / 'app_component_graph.py')
        (package / '__main__.py').write_text('import sys,json\nfrom .application_generations import delegate\ndelegate()\nprint(json.dumps({"prefix":sys.prefix,"args":sys.argv[1:]}))\n')
        metadata = site / f'amplifier_unified-{version}.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text(f'Name: amplifier-unified\nVersion: {version}\n')
    receipt['componentDigest'] = digest(normalized([{'name': 'amplifier-unified', 'version': '99.0.0', 'direct': None}]))
    (folder / 'validated.json').write_text(json.dumps(receipt))
    generations.promote(home, receipt, bootstrap)
    python = bootstrap / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    arguments = ['--data-dir', str(home), '--port', '8999']
    environment = {name: os.environ[name] for name in ('PATH', 'SystemRoot') if name in os.environ}
    environment['AMPLIFIER_WEB_HOME'] = str(home)
    result = subprocess.check_output([str(python), '-I', '-m', 'amplifier_web', *arguments], env=environment, text=True)
    actual = json.loads(result)
    assert Path(actual['prefix']).resolve() == candidate.resolve()
    assert actual['args'] == arguments
