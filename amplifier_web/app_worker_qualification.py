"""Qualify the published app with an already prepared local conversation interpreter."""
import hashlib
import json
import os
import re
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
    graph = record.read_bytes()
    names = {row['name'] for row in json.loads(graph)}
    context = next((name for name in ('context-simple', 'context-managed')
                    if 'amplifier-module-' + name in names), None)
    if not (project / '.venv/bin/python').is_file():
        readiness = 'no-installed-interpreter'
    elif 'amplifier-module-loop-live' not in names or context is None:
        readiness = 'different-runtime-contract'
    else:
        readiness = 'prepared'
    return {'generation': generation, 'project': project.name, 'installedSha256': hashlib.sha256(graph).hexdigest(),
            'context': context, 'readiness': readiness}


async def qualify(home, candidate_python, folder, selection):
    project = project_path(home, selection['generation'])
    packages = list((candidate_python.parent.parent / 'lib').glob('python*/site-packages/amplifier_web'))
    if len(packages) != 1:
        raise ValueError('Cannot identify the selected application package for the worker check.')
    # Use the outgoing check with each app's own bootstrap and host packages.
    # No user home, credentials, provider endpoint, or saved session is passed.
    async def observe(package):
        with TemporaryDirectory(prefix='worker-check-', dir=folder) as scratch:
            output = await process(project / '.venv/bin/python', '-I', '-B',
                Path(__file__).with_name('worker_startup_probe.py'), Path(scratch) / 'trial',
                '--installed-package', package, '--context', selection['context'],
                '--tool-registration', 'retained', '--compare-startup',
                env={'HOME': scratch, 'PATH': os.defpath, 'TMPDIR': scratch}, cwd=scratch, timeout=240)
            report = json.loads(output)
        if report == {'workerStarts': 2, 'resumed': True, 'modelCalls': 0, 'toolCalls': 0,
                      'registration': 'retained', 'sharedRegistryUnchanged': True, 'productionHistoryTouched': False}:
            return None
        if (isinstance(report, dict) and set(report) == {'startupFailed', 'attempt'}
                and isinstance(report['startupFailed'], str) and re.fullmatch('[a-f0-9]{64}', report['startupFailed'])
                and type(report['attempt']) is int and report['attempt'] in (1, 2)):
            return report
        raise ValueError('The retained conversation startup receipt is incomplete.')

    failure = await observe(packages[0])
    if failure is not None:
        # Broken components must not prevent a host-only repair. A successful
        # incumbent or a different failure makes this a candidate regression.
        if await observe(Path(__file__).resolve().parent) != failure:
            raise ValueError('The candidate cannot start or reopen a retained conversation. The current application was kept.')
    if prepared(home) != selection:
        raise ValueError('The retained conversation interpreter changed during qualification.')
    return {'status':'pre-existing-startup-failure', **failure} if failure else {'status':'ready'}
