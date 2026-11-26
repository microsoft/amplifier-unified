"""Architecture boundary checks: the packaged host must not import CLI apps."""
import ast
from itertools import chain
import json
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'amplifier_app_cli', 'amplifier_loop_live_cli', 'amplifier_workspace'}
WORK_MANIFEST = ROOT / 'scripts/work_profile/pyproject.toml'
WORKER_MANIFEST = ROOT / 'amplifier_web/runtime_deps/pyproject.toml'


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
    if path in (ROOT / 'pyproject.toml', WORK_MANIFEST, WORKER_MANIFEST):
        metadata = tomllib.loads(text)
        if path == ROOT / 'pyproject.toml':
            # These two offline qualification groups are not installed runtime
            # defaults. Keep every other metadata section in the scan.
            for group in ('dev', 'work-media'):
                metadata.get('dependency-groups', {}).pop(group, None)
        elif path == WORK_MANIFEST:
            # This separate, non-packaged acceptance project selects tested
            # dependencies; it does not define the ordinary Work runtime.
            metadata.get('project', {}).pop('dependencies', None)
        for name, sources in metadata.get('tool', {}).get('uv', {}).get('sources', {}).items():
            for source in sources if isinstance(sources, list) else [sources]:
                if 'git' in source:
                    assert (source.get('branch') or source.get('rev')) == 'main' and 'tag' not in source, name
        text = json.dumps(metadata)
    fixed = re.compile(r'git\+https://github.com/(?:microsoft|bkrabach)/amplifier[^\s"\'#@]*@(?:[a-f0-9]{7,40}|v?\d+\.[^\s"\']+)')
    assert not fixed.search(text), str(path)


def test_shipped_ecosystem_sources_do_not_freeze_upstream_revisions():
    # CI/qualification dependency selections are not shipped runtime defaults.
    roots = [ROOT / 'amplifier_web', ROOT / 'behaviors', ROOT / 'scripts/work_profile']
    paths = [ROOT / 'pyproject.toml'] + [p for root in roots for p in root.rglob('*')
        if p.suffix in {'.py', '.toml', '.yaml', '.yml', '.txt'} and not any(
            part in {'.venv', 'vendor', 'tests', 'static', '__pycache__'} for part in p.parts)]
    for path in paths:
        assert_unfrozen_sources(path, path.read_text())


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


@pytest.mark.parametrize('revision', ['abcdef0', 'a' * 40, 'v1.2.3'])
def test_work_pin_exemption_is_exact_acceptance_project_dependencies_only(revision):
    requirement = 'fixture @ git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@' + revision
    text = f'[project]\ndependencies = [{json.dumps(requirement)}]\n'
    assert_unfrozen_sources(WORK_MANIFEST, text)
    for path in (ROOT / 'pyproject.toml', WORKER_MANIFEST,
                 ROOT / 'scripts/other/pyproject.toml',
                 ROOT / 'scripts/work_profile/nested/pyproject.toml',
                 ROOT / 'scripts/work_profile/requirements.txt',
                 ROOT / 'scripts/work_profile/acceptance.py',
                 ROOT / 'behaviors/work.yaml'):
        with pytest.raises(AssertionError):
            assert_unfrozen_sources(path, text)


@pytest.mark.parametrize('section,key', [
    ('project.optional-dependencies', 'dev'),
    ('dependency-groups', 'dev'),
    ('dependency-groups', 'work-media'),
    ('tool.uv', 'override-dependencies'),
    ('tool.uv', 'constraint-dependencies'),
    ('build-system', 'requires'),
])
@pytest.mark.parametrize('revision', ['abcdef0', 'a' * 40, 'v1.2.3'])
def test_work_pin_exemption_keeps_other_sections_guarded(section, key, revision):
    requirement = 'fixture @ git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@' + revision
    text = f'[{section}]\n{key} = [{json.dumps(requirement)}]\n'
    with pytest.raises(AssertionError):
        assert_unfrozen_sources(WORK_MANIFEST, text)


@pytest.mark.parametrize('field', ['rev', 'branch', 'tag'])
@pytest.mark.parametrize('revision', ['abcdef0', 'a' * 40, 'v1.2.3'])
@pytest.mark.parametrize('multiple', [False, True])
def test_qualification_exemptions_do_not_freeze_global_source_tables(field, revision, multiple):
    source = ('{ git = "https://github.com/microsoft/amplifier-module-provider-openai-chatgpt", '
              f'{field} = {json.dumps(revision)} }}')
    text = '[tool.uv.sources]\nfixture = ' + (f'[{source}]' if multiple else source) + '\n'
    for path in (ROOT / 'pyproject.toml', WORK_MANIFEST, WORKER_MANIFEST):
        with pytest.raises(AssertionError):
            assert_unfrozen_sources(path, text)


@pytest.mark.parametrize('multiple', [False, True])
def test_branch_tracking_sources_remain_allowed(multiple):
    source = ('{ git = "https://github.com/microsoft/amplifier-module-provider-openai-chatgpt", '
              'rev = "main" }')
    text = '[tool.uv.sources]\nfixture = ' + (f'[{source}]' if multiple else source) + '\n'
    for path in (ROOT / 'pyproject.toml', WORK_MANIFEST, WORKER_MANIFEST):
        assert_unfrozen_sources(path, text)


def assert_no_work_runtime_requirement(metadata):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    project = metadata.get('project', {})
    requirements = list(project.get('dependencies', []))
    for group in project.get('optional-dependencies', {}).values():
        requirements.extend(group)
    requirements.extend(metadata.get('tool', {}).get('uv', {}).get('override-dependencies', []))
    for requirement in requirements:
        assert canonicalize_name(Requirement(requirement).name) != 'amplifier-work-acceptance', requirement


def assert_work_files_not_selected_for_wheel(root, metadata):
    from hatchling.builders.wheel import WheelBuilder

    # Use the backend's actual file selection, not a second glob interpreter.
    # This inspects configured inputs only; it does not run build hooks/builds.
    work = (root / 'scripts/work_profile').resolve()
    builder = WheelBuilder(str(root), config=metadata)
    build_data = builder.get_default_build_data()
    builder.set_build_data_defaults(build_data)
    with builder.config.set_build_data(build_data):
        for item in chain(builder.recurse_included_files(),
                          builder.recurse_explicit_files(builder.config.shared_scripts),
                          builder.recurse_explicit_files(builder.config.shared_data)):
            assert not Path(item.path).resolve().is_relative_to(work), item.distribution_path


def test_work_pin_exemption_requires_separate_nonruntime_project():
    work = tomllib.loads(WORK_MANIFEST.read_text())
    assert work['project']['name'] == 'amplifier-work-acceptance'
    assert work['tool']['uv']['package'] is False
    assert work['tool']['uv']['sources']['amplifier-unified'] == {'path': '../..'}
    root = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    worker = tomllib.loads(WORKER_MANIFEST.read_text())
    for metadata in (root, worker):
        assert_no_work_runtime_requirement(metadata)
    assert_work_files_not_selected_for_wheel(ROOT, root)


@pytest.mark.parametrize('section', ['dependencies', 'optional-dependencies', 'override-dependencies'])
@pytest.mark.parametrize('name', ['amplifier-work-acceptance', 'Amplifier_Work.Acceptance'])
def test_work_packaging_boundary_rejects_runtime_requirements(section, name):
    requirement = f'{name}>=0.1; python_version >= "3.13"'
    if section == 'override-dependencies':
        metadata = {'tool': {'uv': {section: [requirement]}}}
    else:
        value = {'feature': [requirement]} if section == 'optional-dependencies' else [requirement]
        metadata = {'project': {section: value}}
    with pytest.raises(AssertionError):
        assert_no_work_runtime_requirement(metadata)


@pytest.mark.parametrize('selection', [
    {'packages': ['scripts/work_profile']},
    {'only-include': ['scripts']},
    {'include': ['scripts/work_profile/**']},
    {'include': ['runtime/**'], 'artifacts': ['scripts/work_profile/**']},
    {'packages': ['runtime'], 'force-include': {'scripts/work_profile': 'runtime/acceptance'}},
    {'packages': ['runtime'], 'force-include': {'scripts/work_profile/pyproject.toml': 'runtime/qualification.toml'}},
    {'packages': ['runtime'], 'shared-scripts': {'scripts/work_profile/acceptance.py': 'work-acceptance'}},
    {'packages': ['runtime'], 'shared-data': {'scripts/work_profile/pyproject.toml': 'share/qualification.toml'}},
])
def test_work_packaging_boundary_rejects_wheel_inclusion(tmp_path, selection):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / '__init__.py').write_text('')
    work = tmp_path / 'scripts/work_profile'
    work.mkdir(parents=True)
    (work / 'pyproject.toml').write_text('[project]\nname = "amplifier-work-acceptance"\n')
    (work / 'acceptance.py').write_text('# Qualification script, not an installed entry point.\n')
    metadata = {'project': {'name': 'boundary-fixture', 'version': '0.0.0'},
                'tool': {'hatch': {'build': {'targets': {'wheel': {'packages': ['runtime']}}}}}}
    assert_work_files_not_selected_for_wheel(tmp_path, metadata)
    # Replace packages as well, so include/artifacts exercise effective traversal.
    metadata['tool']['hatch']['build']['targets']['wheel'] = selection
    with pytest.raises(AssertionError):
        assert_work_files_not_selected_for_wheel(tmp_path, metadata)
