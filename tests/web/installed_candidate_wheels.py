"""Bounded offline wheel/installed-file checks for the Optimize-only wrapper.

This module never installs or imports candidate code during manifest validation.
The installed probe is copied outside the checkout and run by the fresh venv -I.
"""
import base64
import configparser
import csv
from email.parser import BytesParser
import hashlib
import importlib
import importlib.metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

OWNERS = {'amplifier-foundation': 'amplifier_foundation',
          'amplifier-bundle-context-intelligence': 'context_intelligence'}
MAX_MANIFEST = 32768
MAX_WHEEL = 64 * 1024 * 1024
MAX_MEMBERS = 10000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hex_digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def regular(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink ancestry is not allowed')
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), 'Expected a regular file')
    return path


def relative_name(name):
    require(isinstance(name, str) and name and '\\' not in name and '\x00' not in name,
            'Invalid relative payload path')
    value = PurePosixPath(name)
    require(not value.is_absolute() and all(p not in {'', '.', '..'} for p in name.split('/')),
            'Payload path escape or noncanonical path')
    return name


def record_rows(data):
    rows = {}
    for row in csv.reader(io.StringIO(data.decode('utf-8'))):
        require(len(row) == 3, 'Invalid RECORD row')
        name, encoded, size = row
        relative_name(name)
        require(name not in rows, 'Duplicate RECORD entry')
        rows[name] = (encoded, size)
    return rows


def check_record(data, encoded, size):
    require(encoded.startswith('sha256='), 'RECORD requires SHA-256')
    expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
    require(encoded == 'sha256=' + expected and size == str(len(data)), 'RECORD hash or size mismatch')


def wheel_identity(path, selection):
    path = regular(path)
    require(path.suffix == '.whl' and path.stat().st_size <= MAX_WHEEL, 'Invalid or oversized candidate wheel')
    require(hex_digest(selection['sha256']), 'Invalid wheel SHA-256')
    require(sha(path.read_bytes()) == selection['sha256'], 'Candidate wheel SHA-256 mismatch')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(len(entries) <= MAX_MEMBERS, 'Too many wheel members')
        require(sum(entry.file_size for entry in entries) <= MAX_WHEEL, 'Expanded wheel exceeds bound')
        files = {}
        for entry in entries:
            if entry.is_dir():
                relative_name(entry.filename.rstrip('/'))
                continue
            name = relative_name(entry.filename)
            require(name not in files, 'Duplicate wheel payload')
            mode = entry.external_attr >> 16
            require(stat.S_IFMT(mode) in {0, stat.S_IFREG}, 'Nonregular wheel payload')
            require(not entry.flag_bits & 1, 'Encrypted wheel payload')
            require('.data' not in PurePosixPath(name).parts[0]
                    and not name.endswith(('.pth', '.pyc', '.pyo')), 'Unsupported executable or relocated wheel payload')
            files[name] = archive.read(entry)
        records = [name for name in files if name.endswith('.dist-info/RECORD')]
        require(len(records) == 1, 'Wheel must have one RECORD')
        record = records[0]
        dist_info = record.rsplit('/', 1)[0]
        rows = record_rows(files[record])
        require(set(rows) == set(files), 'RECORD does not cover the whole wheel payload')
        for name, data in files.items():
            if name == record:
                require(rows[name] == ('', ''), 'RECORD self-entry must be unhashed')
            else:
                check_record(data, *rows[name])
        metadata = BytesParser().parsebytes(files[dist_info + '/METADATA'])
        name = re.sub('[-_.]+', '-', metadata['Name'] or '').lower()
        require(name == selection['distribution'], 'Wheel distribution does not match selection')
        wheel_meta = files[dist_info + '/WHEEL'].decode()
        require('Root-Is-Purelib: true' in wheel_meta, 'Only purelib candidate components are supported')
        if dist_info + '/entry_points.txt' in files:
            entry_points = configparser.ConfigParser()
            entry_points.read_string(files[dist_info + '/entry_points.txt'].decode())
            require(not any(entry_points.has_section(group) and list(entry_points[group])
                            for group in ('console_scripts', 'gui_scripts')),
                    'Candidate console scripts are unsupported; no installation was attempted')
        prefix = OWNERS[name]
        require(all(p.startswith((prefix + '/', dist_info + '/')) for p in files),
                'Candidate wheel contains payload outside its component owner')
        modules = selection['keyModules']
        require(isinstance(modules, dict) and 1 <= len(modules) <= 8 and prefix in modules,
                'Candidate must pin its canonical package module')
        for module, detail in modules.items():
            require(re.fullmatch(re.escape(prefix) + r'(?:\.[A-Za-z_]\w*)*', module) is not None,
                    'Key module outside selected component')
            require(isinstance(detail, dict) and set(detail) == {'path', 'sha256'}, 'Invalid key module entry')
            source = relative_name(detail['path'])
            require(source in {module.replace('.', '/') + '.py', module.replace('.', '/') + '/__init__.py'},
                    'Key module path does not match its name')
            require(hex_digest(detail['sha256']) and source in files
                    and sha(files[source]) == detail['sha256'], 'Key module hash mismatch')
        return {**selection, 'wheelPath': str(path), 'version': metadata['Version'],
                'distInfo': dist_info, 'record': record,
                'files': {p: sha(data) for p, data in files.items() if p != record}}


def load_candidates(path):
    path = regular(path)
    require(path.stat().st_size <= MAX_MANIFEST, 'Candidate manifest exceeds bound')
    raw = path.read_bytes()
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'schemaVersion', 'wheels'}
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1,
            'Unsupported candidate manifest schema')
    wheels = value['wheels']
    require(isinstance(wheels, list) and len(wheels) == 2, 'Select exactly Foundation and CI wheels')
    selected = []
    for row in wheels:
        require(isinstance(row, dict) and set(row) ==
                {'distribution', 'file', 'sha256', 'sourceCommit', 'keyModules'}, 'Invalid wheel selection')
        require(row['distribution'] in OWNERS, 'Unrelated candidate distribution')
        require(isinstance(row['sourceCommit'], str)
                and re.fullmatch('[0-9a-f]{40}', row['sourceCommit']), 'Pin an immutable source commit')
        filename = relative_name(row['file'])
        wheel = regular(path.parent / filename)
        require(wheel.is_relative_to(path.parent), 'Candidate wheel escapes its manifest directory')
        selected.append(wheel_identity(wheel, row))
    require({row['distribution'] for row in selected} == set(OWNERS), 'Duplicate/missing selected component')
    return {'manifestSha256': sha(raw), 'candidates': selected}


def installed_files(site, identity):
    """Verify every selected payload and the installer's complete RECORD."""
    site = Path(site).resolve()
    for name, expected in identity['files'].items():
        path = regular(site / relative_name(name))
        require(path.is_relative_to(site) and sha(path.read_bytes()) == expected, 'Installed payload hash mismatch')
    record = regular(site / identity['record'])
    rows = record_rows(record.read_bytes())
    require(set(identity['files']).issubset(rows), 'Installed RECORD omits wheel payload')
    extras = set(rows) - set(identity['files']) - {identity['record']}
    for name, (encoded, size) in rows.items():
        path = regular(site / name)
        require(path.is_relative_to(site), 'Installed RECORD path escape')
        if name == identity['record']:
            require((encoded, size) == ('', ''), 'Invalid installed RECORD self-entry')
        else:
            check_record(path.read_bytes(), encoded, size)
    # uv records these non-code installer receipts too; every byte still needs
    # its installed RECORD hash/size, and no arbitrary metadata exception exists.
    allowed = {identity['distInfo'] + '/' + name for name in
               ('INSTALLER', 'REQUESTED', 'direct_url.json', 'uv_build.json', 'uv_cache.json')}
    require(extras.issubset(allowed), 'Unexpected installer payload')
    # Catch injected or unrecorded files inside the actual installed distribution.
    roots = {PurePosixPath(name).parts[0] for name in identity['files']}
    actual = set()
    for root in roots:
        for path in (site / root).rglob('*'):
            require(not path.is_symlink(), 'Symlink in installed component')
            if path.is_file():
                name = path.relative_to(site).as_posix()
                actual.add(name)
    require(actual == set(rows), 'Unrecorded installed component files')
    return {'distribution': identity['distribution'], 'version': identity['version'],
            'sourceCommit': identity['sourceCommit'], 'wheelSha256': identity['sha256'],
            'payloadFiles': len(identity['files']), 'installedRecordFiles': len(rows),
            'installedRecordSha256': sha(record.read_bytes())}


def installed_probe(plan_path, venv):
    plan = json.loads(regular(plan_path).read_text())
    venv = Path(venv).resolve()
    require(not venv.is_relative_to('/workspace'), 'Qualification venv must be outside /workspace')
    require(sys.flags.isolated == 1 and not any(
        Path(p or '.').resolve().is_relative_to('/workspace') for p in sys.path), 'Probe is not isolated')
    # File validation is an all-candidate admission barrier. Even importing the
    # first package can transitively execute another selected component, so do
    # not interleave imports with validation of the next installed distribution.
    validated = []
    for candidate in plan['candidates']:
        dist = importlib.metadata.distribution(candidate['distribution'])
        site = Path(dist.locate_file('')).resolve()
        require(site.is_relative_to(venv), 'Candidate distribution fell back to prepared dependencies')
        require(dist.version == candidate['version'], 'Installed distribution version mismatch')
        receipt = installed_files(site, candidate)
        validated.append((candidate, site, receipt))
    receipts = []
    for candidate, site, receipt in validated:
        modules = {}
        for name, spec in candidate['keyModules'].items():
            module = importlib.import_module(name)
            path = regular(module.__file__)
            require(path == site / spec['path'] and sha(path.read_bytes()) == spec['sha256'],
                    'Key module origin/hash mismatch')
            modules[name] = {'path': str(path), 'sha256': spec['sha256']}
        receipt['modules'] = modules
        receipts.append(receipt)
    identities = {}
    for name in ('amplifier_foundation', 'context_intelligence', 'amplifier_core._engine', 'amplifier_web.server'):
        module = importlib.import_module(name)
        path = regular(module.__file__)
        require(not path.is_relative_to('/workspace'), 'Module resolved into checkout')
        if name == 'amplifier_web.server' or (plan['candidates'] and name != 'amplifier_core._engine'):
            require(path.is_relative_to(venv), 'Selected application/component module came from prepared fallback')
        identities[name] = {'path': str(path), 'sha256': sha(path.read_bytes()),
                            'inQualificationVenv': path.is_relative_to(venv)}
    distributions = {}
    for name in (*OWNERS, 'amplifier-core', 'amplifier-unified'):
        dist = importlib.metadata.distribution(name)
        distributions[name] = {'version': dist.version, 'location': str(Path(dist.locate_file('')).resolve())}
    return {'verified': True, 'candidates': receipts, 'moduleIdentities': identities,
            'distributions': distributions,
            'coreSelection': 'Observed prepared native dependency; not a new Core selection',
            'componentSelection': 'Explicit offline wheels' if receipts else 'Prepared dependencies observed; no candidate selection',
            'manifestSha256': plan['manifestSha256']}


if __name__ == '__main__':
    require(len(sys.argv) == 4 and sys.argv[1] == '--installed-probe', 'Unsupported probe arguments')
    print(json.dumps(installed_probe(sys.argv[2], sys.argv[3])))