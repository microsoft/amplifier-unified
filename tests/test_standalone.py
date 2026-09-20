"""Architecture boundary checks: the packaged host must not import CLI apps."""
import ast
from pathlib import Path
import tomllib

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


def test_shipped_ecosystem_sources_do_not_freeze_upstream_revisions():
    import re
    roots = [ROOT / 'amplifier_web', ROOT / 'behaviors', ROOT / 'scripts/work_profile', ROOT / '.github']
    paths = [ROOT / 'pyproject.toml'] + [p for root in roots for p in root.rglob('*')
        if p.suffix in {'.py', '.toml', '.yaml', '.yml', '.txt'} and not any(
            part in {'.venv', 'vendor', 'tests', 'static', '__pycache__'} for part in p.parts)]
    fixed = re.compile(r'git\+https://github.com/(?:microsoft|bkrabach)/amplifier[^\s"\'#@]*@(?:[a-f0-9]{7,40}|v?\d+\.[^\s"\']+)')
    for path in paths:
        assert not fixed.search(path.read_text()), str(path)
    manifest = tomllib.loads((ROOT / 'amplifier_web/runtime_deps/pyproject.toml').read_text())
    for name, source in manifest['tool']['uv']['sources'].items():
        if 'git' in source:
            assert (source.get('branch') or source.get('rev')) == 'main' and 'tag' not in source, name
