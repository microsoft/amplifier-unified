"""Architecture boundary checks: the packaged host must not import CLI apps."""
import ast
import json
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'amplifier_app_cli', 'amplifier_loop_live_cli', 'amplifier_workspace'}


def test_no_cli_host_imports():
    for path in (ROOT / 'amplifier_web').rglob('*.py'):
        if '.venv' in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
            assert not any(name.split('.')[0] in FORBIDDEN for name in names), str(path)


def test_no_cli_distribution_dependencies():
    for path in [ROOT / 'pyproject.toml', ROOT / 'amplifier_web/runtime_deps/pyproject.toml']:
        data = tomllib.loads(path.read_text())
        for requirement in data['project']['dependencies']:
            assert not any(name.replace('_', '-') in requirement for name in FORBIDDEN), requirement


def test_generation_completion_keeps_delivered_and_accepted_distinct():
    from amplifier_web.runtime import normalize_event
    kind, payload = normalize_event({'type': 'generation.finished', 'generation_id': 'g',
        'input_ids': ['delivered'], 'accepted_input_ids': ['queued'], 'text': 'Result',
        'active_job_ids': ['j'], 'disposition': 'manager_turn_finished', 'provider_payload': 'private'}, 's')
    assert kind == 'runtime.generation'
    assert payload['input_ids'] == ['delivered']
    assert payload['accepted_input_ids'] == ['queued']
    assert payload['active_job_ids'] == ['j']
    assert 'provider_payload' not in payload


def assert_unfrozen_sources(path, text):
    import re
    if path == ROOT / 'pyproject.toml':
        # These two offline qualification groups are not installed runtime
        # defaults. Keep every other metadata section in the scan.
        metadata = tomllib.loads(text)
        for group in ('dev', 'work-media'):
            metadata.get('dependency-groups', {}).pop(group, None)
        text = json.dumps(metadata)
    fixed = re.compile(r'git\+https://github.com/(?:microsoft|bkrabach)/amplifier[^\s"\'#@]*@(?:[a-f0-9]{7,40}|v?\d+\.[^\s"\']+)')
    assert not fixed.search(text), str(path)


def test_shipped_ecosystem_sources_do_not_freeze_upstream_revisions():
    # CI workflows qualify exact inputs; they are not shipped runtime sources.
    roots = [ROOT / 'amplifier_web', ROOT / 'behaviors', ROOT / 'scripts/work_profile']
    paths = [ROOT / 'pyproject.toml'] + [p for root in roots for p in root.rglob('*')
        if p.suffix in {'.py', '.toml', '.yaml', '.yml', '.txt'} and not any(
            part in {'.venv', 'vendor', 'tests', 'static', '__pycache__'} for part in p.parts)]
    for path in paths:
        assert_unfrozen_sources(path, path.read_text())
    manifest = tomllib.loads((ROOT / 'amplifier_web/runtime_deps/pyproject.toml').read_text())
    for name, source in manifest['tool']['uv']['sources'].items():
        if 'git' in source:
            assert (source.get('branch') or source.get('rev')) == 'main' and 'tag' not in source, name


@pytest.mark.parametrize('group', ['dev', 'work-media'])
def test_offline_pin_exemption_is_root_fixture_group_only(group):
    requirement = 'fixture @ git+https://github.com/microsoft/amplifier-bundle-context-managed@' + 'a' * 40 + '#subdirectory=modules/tool-transcript'
    text = f'[dependency-groups]\n{group} = [{json.dumps(requirement)}]\n'
    assert_unfrozen_sources(ROOT / 'pyproject.toml', text)
    with pytest.raises(AssertionError):
        assert_unfrozen_sources(ROOT / 'amplifier_web/runtime_deps/pyproject.toml', text)


@pytest.mark.parametrize('section,key', [
    ('project', 'dependencies'),
    ('project.optional-dependencies', 'work-media'),
    ('dependency-groups', 'other'),
    ('tool.uv', 'override-dependencies'),
])
@pytest.mark.parametrize('revision', ['abcdef0', 'a' * 40, 'v1.2.3'])
def test_fixture_pin_exemption_keeps_shipped_requirements_guarded(section, key, revision):
    requirement = 'fixture @ git+https://github.com/microsoft/amplifier-bundle-context-managed@' + revision + '#subdirectory=modules/tool-transcript'
    text = f'[{section}]\n{key} = [{json.dumps(requirement)}]\n'
    for path in (ROOT / 'pyproject.toml', ROOT / 'amplifier_web/runtime_deps/pyproject.toml'):
        with pytest.raises(AssertionError):
            assert_unfrozen_sources(path, text)
