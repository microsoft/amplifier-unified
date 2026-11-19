"""Qualify the published app with an already prepared local conversation interpreter."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .runtime_environment import project_path, receipt_directory
from .updates import active_release, process


def prepared(home):
    # The POSIX media construction contract is the cross-environment feature
    # checked here. First setup and pre-generation runtimes have no recorded
    # interpreter to qualify. App recovery never provisions one for this check.
    if os.name != 'posix':
        return None
    generation = active_release(home).get('current')
    receipt = receipt_directory(home, generation)
    record = receipt / 'runtime-installed.json'
    if not record.is_file():
        return None
    project = project_path(home, generation)
    python = project / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise ValueError('The recorded conversation interpreter is missing. Repair the installation before updating.')
    return {'generation': generation, 'project': project.name,
            'installedSha256': hashlib.sha256(record.read_bytes()).hexdigest()}

async def qualify(home, candidate_python, folder, selection):
    project = project_path(home, selection['generation'])
    packages = list((candidate_python.parent.parent / 'lib').glob('python*/site-packages/amplifier_web'))
    if len(packages) != 1:
        raise ValueError('Cannot identify the selected application package for the worker check.')
    # Use the outgoing check with the candidate's bootstrap and host packages.
    # No user home, credentials, provider endpoint, or saved session is passed.
    with TemporaryDirectory(prefix='worker-check-', dir=folder) as scratch:
        env = {'HOME': scratch, 'PATH': os.defpath, 'TMPDIR': scratch}
        output = await process(project / '.venv/bin/python', '-I', '-B',
            Path(__file__).with_name('worker_startup_probe.py'), Path(scratch) / 'trial',
            '--installed-package', packages[0], '--tool-registration', 'retained',
            env=env, cwd=scratch, timeout=60)
        report = json.loads(output)
    if report != {
        'workerStarts': 2, 'resumed': True, 'modelCalls': 0, 'toolCalls': 0,
        'registration': 'retained', 'sharedRegistryUnchanged': True, 'productionHistoryTouched': False,
    } or prepared(home) != selection:
        raise ValueError('The retained conversation startup or its selected interpreter could not be verified.')
    return selection
