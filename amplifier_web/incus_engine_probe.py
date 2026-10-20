"""Bounded read-only source/selection probe for an existing Incus installation.

Sent as fixed app code to an already approved SSH target; it writes no files and
never installs a controller, imports app code, or changes daemon configuration.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

REPOSITORY = 'https://github.com/microsoft/amplifier-bundle-digital-twin-universe'
LEGACY_COMMIT = 'ce9219d8dac2770efe0b8270acd8ec017af625c7'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_binding(config):
    return {key: value for key, value in config.items() if key not in {'maxRunning', 'maxInstances', 'minFreeBytes'}}


def read(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('The engine settings path changed.')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 65536):
            raise ValueError('The engine settings are not private owned files.')
        return json.load(stream)


def configuration_path(value=''):
    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    path = Path(value) if value else home / '.amplifier-unified/test-environments.json'
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Use the registered settings path.')
    return path


def interpreter(config):
    path = Path(config['executable'])
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('The engine launcher changed.')
    info = path.stat()
    content = path.read_bytes()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022 or len(content) > 65536 or hashlib.sha256(content).hexdigest() != config['executableSha256']:
        raise ValueError('The engine launcher changed.')
    lines = content.decode().splitlines()
    if not lines:
        raise ValueError('The engine launcher is empty.')
    if lines[0] == '#!/bin/sh':
        match = re.fullmatch(r"'''exec' (.+) \"\$0\" \"\$@\"", lines[1]) if len(lines) > 1 else None
        if len(lines) < 3 or lines[2] != "' '''":
            raise ValueError('The engine launcher has an unsupported shell wrapper.')
        words = shlex.split(match.group(1)) if match else []
        python = Path(words[0]) if len(words) == 1 else None
    else:
        python = Path(lines[0][2:]) if lines[0].startswith('#!/') else None
    if not python or not python.is_absolute() or not python.is_file() or not re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', python.name):
        raise ValueError('The engine has no explicit installed interpreter.')
    return python


METADATA = '''import importlib.metadata,json
p=importlib.metadata.distribution("amplifier-bundle-digital-twin-universe")
print(json.dumps({"version":p.version,"directUrl":json.loads(p.read_text("direct_url.json") or "{}")}))'''


def installed(config):
    result = subprocess.run([str(interpreter(config)), '-I', '-B', '-c', METADATA],
        env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('The engine source could not be read.')
    value = json.loads(result.stdout)
    direct = value.get('directUrl') or {}
    vcs = direct.get('vcs_info') or {}
    if (vcs.get('vcs') != 'git' or direct.get('url', '').removesuffix('.git') != REPOSITORY
            or not re.fullmatch('[a-f0-9]{40}', vcs.get('commit_id', ''))):
        return {'revision': None, 'packageVersion': value['version']}
    return {'revision': vcs['commit_id'], 'packageVersion': value['version']}


def policy(path, config, revision):
    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    selections = home / '.local/state/amplifier-test-runners/engine-updates'
    direct = selections / ('selection-' + digest(config) + '.json')
    for receipt in dict.fromkeys([direct, *sorted(selections.glob('selection-*.json'))[:256]]):
        carried = read(receipt)
        if (carried and source_binding(carried.get('configuration', {})) == source_binding(config)
                and carried.get('revision') == revision):
            return carried.get('policy')
    folder = path.parent / 'test-runner-setup'
    for index, receipt in enumerate(sorted(folder.glob('*.json'))):
        if index >= 256:
            break
        if not re.fullmatch(r'[a-f0-9]{32}\.json', receipt.name):
            continue
        value = read(receipt) or {}
        if (value.get('state') == 'completed' and source_binding(value.get('candidateConfiguration', {}).get('incus', {})) == source_binding(config)
                and 'priorConfiguration' in value and value['priorConfiguration'] is None
                and value.get('proof', {}).get('probeRemoved') is True):
            declared = value.get('engineSourcePolicy')
            if declared is not None:
                return declared if isinstance(declared, dict) else None
            if revision == LEGACY_COMMIT:
                return {'version': 1, 'repository': REPOSITORY, 'ref': 'main', 'origin': 'app-setup', 'setupId': value['id']}
    return None


def inspect(value):
    if not isinstance(value, dict) or set(value) != {'configPath', 'expectedIncus'}:
        raise ValueError('Unsupported engine inspection.')
    path = configuration_path(value['configPath'])
    raw = read(path)
    if not raw or raw.get('enabled') is not True or raw.get('backend') != 'incus' or raw.get('incus') != value['expectedIncus']:
        raise ValueError('The selected engine settings changed.')
    observed = installed(raw['incus'])
    return {'version': 1, 'configuration': raw['incus'], **observed,
            'policy': policy(path, raw['incus'], observed['revision']) if observed['revision'] else None}


if __name__ == '__main__':
    print(json.dumps(inspect(json.loads(sys.stdin.buffer.read(65537))), sort_keys=True))
