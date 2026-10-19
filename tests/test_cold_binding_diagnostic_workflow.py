"""Nine static controls for the opt-in diagnostic; never execute a workflow."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FULL_GATE = "${{ github.event_name != 'workflow_dispatch' || inputs.checks != 'cold-binding-diagnostic' }}"
DIAGNOSTIC_GATE = "${{ github.event_name == 'workflow_dispatch' && inputs.checks == 'cold-binding-diagnostic' }}"
PREAMBLE = 'mkdir -p output/python-checks\nset -o pipefail\n'
PYTEST = 'uv run --no-sync pytest -v --tb=short --junitxml=output/python-checks/junit.xml'
PIPE = '2>&1 | tee output/python-checks/pytest.log\n'


@pytest.fixture
def workflow():
    # BaseLoader preserves the Actions `on` key and reads scalar values as text.
    return yaml.load(
        (ROOT / '.github/workflows/python-checks.yml').read_text(encoding='utf-8'),
        Loader=yaml.BaseLoader,
    )


def _step(workflow, name):
    matches = [step for step in workflow['jobs']['python']['steps']
               if step.get('name') == name]
    assert len(matches) == 1, name
    return matches[0]


def test_manual_checks_are_exact_fixed_choices_with_full_default(workflow):
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert set(inputs) == {'checks', 'core_wheels'}
    assert inputs['checks'] == {
        'description': 'Select the complete suite or the fixed cold-binding diagnostic',
        'type': 'choice',
        'default': 'full',
        'options': ['full', 'cold-binding-diagnostic'],
    }
    assert inputs['core_wheels'] == {
        'description': 'Qualify published Core wheels and real runtime on both Linux architectures',
        'type': 'boolean',
        'default': 'false',
    }


def test_full_and_diagnostic_gates_are_exact_complements(workflow):
    full = _step(workflow, 'Run the complete Python suite')
    diagnostic = _step(workflow, 'Run the fixed cold-binding diagnostic')
    assert full['if'] == FULL_GATE
    assert diagnostic['if'] == DIAGNOSTIC_GATE
    # No per-step failure suppression, input interpolation, or setup override.
    assert set(full) == {'name', 'if', 'run'}
    assert set(diagnostic) == {'name', 'if', 'env', 'run'}


def test_diagnostic_has_only_five_fixed_files_and_retained_evidence(workflow):
    step = _step(workflow, 'Run the fixed cold-binding diagnostic')
    assert step['env'] == {
        'UNIFIED_COLD_BINDING_DIAGNOSTIC_DIR': 'output/python-checks/cold-binding-diagnostic',
    }
    files = (
        'tests/test_cold_binding_sqlite_lifecycle.py',
        'tests/test_cold_binding_diagnostic_workflow.py',
        'tests/test_cold_work_binding.py',
        'tests/test_work_windows.py',
        'tests/test_resident_work_pages.py',
        'tests/test_regular_work_details.py',
        'tests/test_receipt_authority.py',
        'tests/test_diagnostics.py',
    )
    # Historical test ID retained; the seven existing files remain, with the
    # complete diagnostics module added. No free-form selection or default change.
    assert len(files) == 8
    expected = PREAMBLE + PYTEST + ' \\\n'
    expected += ''.join('  ' + path + ' \\\n' for path in files[:-1])
    expected += '  ' + files[-1] + ' ' + PIPE
    assert step['run'] == expected


def test_normal_full_suite_command_is_unchanged(workflow):
    step = _step(workflow, 'Run the complete Python suite')
    assert step['run'] == PREAMBLE + PYTEST + ' ' + PIPE


def test_core_wheels_routing_is_unchanged(workflow):
    assert set(workflow['jobs']) == {'python', 'core-wheels'}
    assert workflow['jobs']['python']['if'] == '${{ !inputs.core_wheels }}'
    assert workflow['jobs']['core-wheels'] == {
        'if': '${{ inputs.core_wheels }}',
        'uses': './.github/workflows/core-wheels.yml',
        'secrets': {'LOOP_LIVE_CI_READ_KEY': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'},
    }


def test_reusable_inputs_and_secret_contract_are_unchanged(workflow):
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert workflow['on']['workflow_call'] == {
        'secrets': {'LOOP_LIVE_CI_READ_KEY': {'required': 'true'}},
        'inputs': {'revision': {'type': 'string', 'required': 'true'}},
    }
    assert set(workflow['on']['workflow_dispatch']) == {'inputs'}


def test_permissions_platform_and_ninety_minute_budget_are_unchanged(workflow):
    assert set(workflow) == {'name', 'on', 'permissions', 'jobs'}
    assert workflow['name'] == 'Full Python checks'
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['python']
    assert set(job) == {'if', 'runs-on', 'timeout-minutes', 'steps'}
    assert job['runs-on'] == 'ubuntu-latest'
    assert job['timeout-minutes'] == '90'


def test_setup_and_dependency_steps_are_unchanged(workflow):
    assert workflow['jobs']['python']['steps'][:8] == [
        {'uses': 'actions/checkout@v4', 'with': {'ref': '${{ inputs.revision || github.sha }}'}},
        {'uses': 'actions/setup-python@v5', 'with': {'python-version': '3.13'}},
        {'uses': 'astral-sh/setup-uv@v6'},
        {'run': 'uv sync --locked --group dev --group artifacts'},
        {
            'name': 'Resolve the declared live-runtime source',
            'id': 'runtime-dependency',
            'run': (
                'python -c \'import tomllib; p=tomllib.load(open("amplifier_web/runtime_deps/pyproject.toml", "rb")); '
                'print("revision=" + p["tool"]["uv"]["sources"]["amplifier-module-loop-live"]["rev"])\' '
                '>> "$GITHUB_OUTPUT"\n'
            ),
        },
        {
            'uses': 'actions/checkout@v4',
            'with': {
                'repository': 'microsoft/amplifier-module-loop-live',
                'ref': '${{ steps.runtime-dependency.outputs.revision }}',
                'path': '.ci/loop-live',
                'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}',
                'persist-credentials': 'false',
            },
        },
        {
            'name': 'Install runtime prerequisites for host and child tests',
            'run': (
                'uv pip install --python .venv/bin/python .ci/loop-live '
                "'amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@main' "
                "'amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@main' "
                "'amplifier-module-tool-delegate @ git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate' "
                "'amplifier-module-tool-filesystem @ git+https://github.com/microsoft/amplifier-module-tool-filesystem@main' "
                "'amplifier-module-tool-apply-patch @ git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=modules/tool-apply-patch'"
            ),
        },
        {
            'name': 'Require native file-policy modules and Core entry points',
            'run': dedent("""\
                uv run --no-sync python - <<'PY'
                from importlib import import_module
                from importlib.metadata import entry_points
                available = {entry.name: entry for entry in entry_points(group='amplifier.modules')}
                for name in ('loop-live', 'context-simple', 'tool-filesystem', 'tool-apply-patch'):
                    module = import_module('amplifier_module_' + name.replace('-', '_'))
                    mount = available[name].load()
                    if not callable(mount) or mount is not module.mount:
                        raise RuntimeError('Native module entry point is not its canonical mount: ' + name)
                PY
                """),
        },
    ]


def test_artifact_retention_and_step_order_are_unchanged(workflow):
    steps = workflow['jobs']['python']['steps']
    assert len(steps) == 11
    assert [step['name'] for step in steps[8:]] == [
        'Run the complete Python suite',
        'Run the fixed cold-binding diagnostic',
        'Retain complete and partial Python results',
    ]
    assert steps[-1] == {
        'name': 'Retain complete and partial Python results',
        'if': 'always()',
        'uses': 'actions/upload-artifact@v4',
        'with': {
            'name': 'python-results-${{ github.run_attempt }}',
            'path': 'output/python-checks/',
            'if-no-files-found': 'warn',
        },
    }
