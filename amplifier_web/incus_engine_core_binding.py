"""Compose exact prior core proof with a separately qualified native engine.

This records the two original evidence sources. It does not claim that the core
was rerun. Both immutable runtimes and every unrelated execution setting must
still match before the future selection is written.
"""
from pathlib import Path
import re
import subprocess
import json
import os

from amplifier_web import incus_engine_generations as generations
from amplifier_web import incus_engine_probe as probe
from amplifier_web import incus_environment_worker as worker
from amplifier_web import test_runner_controller as controller
from amplifier_web.managed_runner_binding import STATIC_CORE_COMMITS, execution_binding, selection


def runtime_identity(python):
    result = subprocess.run([str(python), '-I', '-B', str(Path(__file__).with_name('managed_runner_identity.py'))],
                            env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, capture_output=True, timeout=120)
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError('The existing core runtime could not be verified.')
    return json.loads(result.stdout)


def policy(directory, current):
    existing = selection(directory, current, probe.read)
    if existing:
        return existing.get('policy')
    for index, path in enumerate(sorted((directory.parent / 'test-runner-setup').glob('*.json'))):
        if index >= 256:
            break
        if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
            continue
        item = probe.read(path) or {}
        if (item.get('state') == 'completed' and execution_binding(item.get('candidateConfiguration', {})) == execution_binding(current)
                and item.get('priorConfiguration', 'unknown') is None
                and item.get('installationId') == current.get('installationId')
                and item.get('proof', {}).get('probeRemoved') is True):
            declared = item.get('runnerSourcePolicy')
            if declared is not None:
                return declared
            if current.get('commit') in STATIC_CORE_COMMITS:
                return {'version': 1, 'repository': 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe',
                        'ref': 'main', 'origin': 'app-setup', 'setupId': item['id']}
    return None


def compose(config_path, current, candidate, engine_path):
    """Write an exact additional proof, before the caller's settings CAS."""
    if {k: v for k, v in current.items() if k != 'incus'} != {k: v for k, v in candidate.items() if k != 'incus'}:
        raise ValueError('Engine publication cannot change other runner settings.')
    generations.verify(candidate['incus'])
    if Path(candidate['incus']['engineQualification']['receipt']).parent != engine_path:
        raise ValueError('The composed engine proof has a different owner.')
    identity = generations.read(engine_path / 'identity.json')
    engine = generations.read(engine_path / 'qualification.json')
    directory = Path(config_path).parent / 'managed-runner-updates'
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('The core qualification store changed.')
    installation = current.get('installationId', '')
    revision = current.get('commit', '')
    if not re.fullmatch('[a-f0-9]{24}', installation) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('The existing core installation identity is unavailable.')
    prefix = 'qualified-' + installation + '-' + revision + '-'
    retained = list(directory.glob(prefix + '*.json'))
    dynamic = current.get('runnerQualificationRequired') is True
    if not dynamic and (revision not in STATIC_CORE_COMMITS or retained):
        raise ValueError('The existing core qualification requirement is ambiguous.')
    directory.mkdir(mode=0o700, exist_ok=True)
    worker.private_directory(directory)
    carried = policy(directory, current)
    if dynamic:
        python = directory.parent / 'smart-tools/installs' / installation / 'venv/bin/python'
        normalized = {**current, 'backend': 'incus', 'python': str(python),
                      'incus': worker.validate_configuration(current['incus'])}
        old_path = directory / (prefix + generations.digest(execution_binding(normalized)) + '.json')
        old = generations.read(old_path)
        if (old.get('version') != 1 or execution_binding(old.get('configuration', {})) != execution_binding(current)
                or execution_binding(old.get('normalized', {})) != execution_binding(normalized)
                or not all(old.get('proof', {}).get(key) is True for key in ('checkPassed', 'artifactVerified', 'probeRemoved'))
                or runtime_identity(python) != old.get('runtime')):
            raise ValueError('The exact existing core qualification changed.')
        replacement = {**normalized, 'incus': worker.validate_configuration(candidate['incus'])}
        receipt = {'version': 1, 'configuration': candidate, 'normalized': replacement,
                   'runtime': old['runtime'], 'updateId': engine_path.name,
                   'proof': {'version': 1, 'kind': 'composed-core-engine',
                             'checkPassed': True, 'artifactVerified': True, 'probeRemoved': True,
                             'backendIdentity': identity['backendIdentity'], 'snapshot': engine['snapshot']},
                   'composition': {'core': {'receipt': str(old_path), 'sha256': generations.digest(old)},
                                   'engine': {'receipt': str(engine_path / 'qualification.json'), 'sha256': generations.digest(engine)}}}
        new_path = directory / (prefix + generations.digest(execution_binding(replacement)) + '.json')
        previous = probe.read(new_path)
        if previous is not None and previous != receipt:
            raise ValueError('The composed runner proof conflicts with retained evidence.')
        controller.write(new_path, receipt)
    if carried is not None:
        old_selection = selection(directory, current, probe.read) or {}
        controller.write(directory / ('selection-' + generations.digest(candidate) + '.json'),
                         {'configuration': candidate, 'policy': carried, 'engineUpdateId': engine_path.name,
                          **({'updateId': old_selection['updateId'],
                              'coreConfiguration': old_selection.get('coreConfiguration', current)}
                             if old_selection.get('updateId') else {})})
