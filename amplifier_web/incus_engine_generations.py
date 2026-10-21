"""Exact private Incus engine generations, separate from moving source policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

REPOSITORY = 'https://github.com/microsoft/amplifier-bundle-digital-twin-universe'
IDENTITY_FIELDS = ('executable', 'executableSha256', 'packageSha256', 'incusExecutable',
                   'configDir', 'admissionDir', 'project', 'daemonFingerprint')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def root():
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/amplifier-test-runners/engine-updates'


def read(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('The engine receipt path changed.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536):
            raise ValueError('Engine receipts must be private owned files.')
        return json.load(stream)


def runtime_identity(python):
    script = Path(__file__).with_name('managed_runner_identity.py')
    result = subprocess.run([str(python), '-I', '-B', str(script), '--incus-engine'],
        env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, capture_output=True, timeout=60)
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError('The engine runtime could not be verified.')
    value = json.loads(result.stdout)
    if value.get('version') != 1 or not re.fullmatch('[a-f0-9]{64}', value.get('runtimeSha256', '')):
        raise ValueError('The engine runtime has no exact identity.')
    return value


def binding(config):
    return {key: config.get(key) for key in IDENTITY_FIELDS}


def identity(config):
    reference = config.get('engineQualification')
    if (not isinstance(reference, dict) or set(reference) != {'version', 'receipt', 'sha256'}
            or reference['version'] != 1 or not re.fullmatch('[a-f0-9]{64}', reference.get('sha256', ''))):
        raise ValueError('The managed engine qualification is invalid.')
    path = Path(reference['receipt'])
    if (path.name != 'identity.json' or path.parent.parent != root()
            or not re.fullmatch('[a-f0-9]{32}', path.parent.name)):
        raise ValueError('The engine generation is outside its owned store.')
    value = read(path)
    if (digest(value) != reference['sha256'] or value.get('version') != 1
            or value.get('configuration') != binding(config)
            or value.get('repository') != REPOSITORY
            or not re.fullmatch('[a-f0-9]{40}', value.get('revision', ''))
            or value.get('owner') != 'engine-update-' + path.parent.name):
        raise ValueError('The exact engine generation changed.')
    return path, value


def verify(config, *, owner=None, runtime=True):
    """Candidate admission is private and restricted to its exact updater owner."""
    path, value = identity(config)
    proof_path = path.with_name('qualification.json')
    proof = read(proof_path) if proof_path.exists() else {}
    qualified = (proof.get('identity') == config['engineQualification']['sha256']
                 and proof.get('version') == 1
                 and proof.get('machineId') == value.get('machineId')
                 and proof.get('backendIdentity') == value.get('backendIdentity')
                 and proof.get('checkPassed') is True and proof.get('artifactVerified') is True
                 and proof.get('probeRemoved') is True)
    if not qualified and owner != value['owner']:
        raise ValueError('This engine generation has not completed its owned qualification.')
    if runtime and runtime_identity(value['python']) != value.get('runtime'):
        raise ValueError('The qualified engine or its dependencies changed.')
    return value
