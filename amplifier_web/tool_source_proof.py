"""Bounded reference-package proof from the worker's existing qualification.

RECORD is a consistency inventory, not publisher authority. The trusted worker
captures its already verified selection; this object only binds that selection
to the actual installed and mounted package bytes. No resolver, Git or installer
is invoked while asking for, or applying, an approval.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import unquote, urlsplit

_PACKAGES = {'amplifier-module-tool-filesystem': 'amplifier_module_tool_filesystem',
             'amplifier-module-tool-apply-patch': 'amplifier_module_tool_apply_patch'}
_LIMIT = 4 * 1024 * 1024
_FILES = 128


def _require(value):
    if not value:
        raise ValueError('The selected tool package cannot be verified.')


def _read(path, root, limit=_LIMIT):
    path, root = Path(path), Path(root)
    _require(path.is_absolute() and path.is_relative_to(root))
    current = path
    while True:
        _require(not current.is_symlink())
        if current == root:
            break
        current = current.parent
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_size <= limit)
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    latest = path.stat(follow_symlinks=False)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    _require(len(content) == before.st_size and all(
        getattr(before, key) == getattr(after, key) == getattr(latest, key) for key in fields))
    return content


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _inventory(package):
    """Read just one small package, including resources and unexpected files."""
    result, size, count = {}, 0, 0
    def unreadable(error):
        raise error
    for directory, directories, files in os.walk(package, onerror=unreadable):
        for name in directories + files:
            path = Path(directory) / name
            count += 1
            _require(count <= _FILES and not path.is_symlink())
        for name in files:
            path = Path(directory) / name
            if path.parent.name == '__pycache__' and path.suffix == '.pyc':
                continue
            raw = _read(path, package, _LIMIT - size)
            size += len(raw)
            result[path.relative_to(package).as_posix()] = (_digest(raw), len(raw))
    _require('__init__.py' in result)
    return result


def _record(distribution, package):
    raw = distribution.read_text('RECORD')
    _require(isinstance(raw, str) and len(raw.encode()) <= _LIMIT)
    rows, names = {}, set()
    for row in csv.reader(io.StringIO(raw), strict=True):
        _require(len(row) == 3)
        name, digest, size = row
        _require(name not in names)
        names.add(name)
        if not name.startswith(package + '/'):
            continue
        _require('\\' not in name and '\0' not in name and ':' not in name
                 and '..' not in PurePosixPath(name).parts and str(PurePosixPath(name)) == name)
        relative = name[len(package) + 1:]
        if PurePosixPath(relative).parent.name == '__pycache__' and relative.endswith('.pyc'):
            continue
        _require(digest.startswith('sha256=') and size.isdecimal() and len(rows) < _FILES)
        decoded = base64.b64decode(digest[7:] + '=' * (-len(digest[7:]) % 4),
                                   altchars=b'-_', validate=True)
        _require(len(decoded) == 32)
        rows[relative] = (decoded.hex(), int(size))
    return rows


class QualifiedToolSources:
    """Private, process-bound witness; never accepted from a tool or saved event."""
    @classmethod
    def capture(cls, project, receipt, expected):
        try:
            value = cls()
            value.project, value.receipt = Path(project), Path(receipt)
            value.prefix = value.project / '.venv'
            _require(value.prefix.resolve() == Path(sys.prefix).resolve()
                     and not value.project.is_symlink() and not value.prefix.is_symlink()
                     and re.fullmatch(r'q-[a-f0-9]{32}(?:-[a-f0-9]{8})?', value.project.name))
            paths = [value.receipt / name for name in
                     ('runtime-project.json', 'runtime-installed.json', 'runtime.lock')]
            paths.append(value.project / 'uv.lock')
            contents = {path: _read(path, path.parent) for path in paths}
            _require(json.loads(contents[paths[0]]) == {'project': value.project.name}
                     and json.loads(contents[paths[1]]) == expected
                     and contents[paths[2]] == contents[paths[3]])
            value.stamps = {path: _digest(raw) for path, raw in contents.items()}
            _require(isinstance(expected, list))
            value.rows = {}
            for row in expected:
                if row.get('name') in _PACKAGES:
                    _require(row['name'] not in value.rows)
                    value.rows[row['name']] = json.loads(json.dumps(row))
            value.payloads = {}
            for name in value.rows:
                try:
                    distribution = importlib.metadata.distribution(name)
                    row, direct, installed = value._installed(name, distribution)
                    payload = _inventory(installed)
                    _require(payload == _record(distribution, _PACKAGES[name]))
                    # verify_recorded has just checked this cache source's
                    # revision, clean checkout and tracked-content digest.
                    selected = value._selected_package(row, direct, _PACKAGES[name])
                    _require(_inventory(selected) == payload)
                    value.payloads[name] = payload
                except (OSError, ValueError, TypeError, KeyError, AttributeError,
                        csv.Error, importlib.metadata.PackageNotFoundError):
                    pass  # One unsupported tool does not disable other tools.
            return value
        except (OSError, ValueError, TypeError, KeyError, AttributeError, csv.Error):
            return None  # Optional proof never prevents an otherwise valid mount.

    def same(self, other):
        return (type(other) is QualifiedToolSources and self.project == other.project
                and self.receipt == other.receipt and self.stamps == other.stamps
                and self.rows == other.rows and self.payloads == other.payloads)

    def check(self):
        _require(Path(sys.prefix).resolve() == self.prefix.resolve())
        for path, digest in self.stamps.items():
            _require(_digest(_read(path, path.parent)) == digest)

    def _installed(self, name, distribution):
        package = _PACKAGES[name]
        _require(name in self.rows)
        row = self.rows[name]
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        _require(distribution.metadata['Name'].lower().replace('_', '-') == name
                 and distribution.version == row['version'] and direct == row.get('directUrl', {})
                 and direct.get('dir_info', {}).get('editable', False) is False)
        installed = Path(distribution.locate_file(package))
        _require(installed.is_absolute() and installed.is_relative_to(self.prefix)
                 and not installed.is_symlink())
        return row, direct, installed

    def _selected_package(self, row, direct, package):
        cached = row.get('cacheSource')
        _require(isinstance(cached, dict) and cached.get('dirty') is False
                 and re.fullmatch('[a-f0-9]{40}', cached.get('revision', '')))
        selected = urlsplit(direct.get('url', ''))
        _require(selected.scheme == 'file' and selected.netloc in ('', 'localhost'))
        build = Path(unquote(selected.path))
        _require(build.is_absolute() and str(build) == row.get('path'))
        self._cache_root(build, cached, build=True)
        candidates = [path for path in (build / package, build / 'src' / package)
                      if (path / '__init__.py').is_file()]
        _require(len(candidates) == 1)
        return candidates[0]

    def _cache_root(self, module, cached, *, build=False):
        subdirectory = cached.get('subdirectory')
        _require(isinstance(subdirectory, str) and not PurePosixPath(subdirectory).is_absolute()
                 and '..' not in PurePosixPath(subdirectory).parts)
        root = module
        for _ in PurePosixPath(subdirectory).parts:
            root = root.parent
        home = self.project.parent.parent
        allowed = [home / 'source-store/objects', self.receipt / 'foundation/cache']
        shared = os.environ.get('AMPLIFIER_SOURCE_STORE')
        if shared:
            allowed.append(Path(shared).expanduser().resolve() / 'objects')
        if build:
            allowed.append(home / 'source-store/builds')
            if shared:
                allowed.append(Path(shared).expanduser().resolve() / 'builds')
        _require(any(root.is_relative_to(base) for base in allowed))
        metadata = json.loads(_read(root / '.amplifier_cache_meta.json', root))
        _require(metadata.get('commit') == cached['revision']
                 and metadata.get('git_url') == cached['url'])

    def describe(self, name, distribution, origin):
        self.check()
        package = _PACKAGES[name]
        row, direct, installed = self._installed(name, distribution)
        _require(name in self.payloads)
        payload = self.payloads[name]
        _require(_inventory(installed) == payload == _record(distribution, package))
        selected = self._selected_package(row, direct, package)
        _require(_inventory(selected) == payload)
        origin = Path(origin)
        # The known class's module path is supplied by the caller, never input.
        mounted = next((parent for parent in origin.parents if parent.name == package), None)
        _require(mounted is not None and origin.is_file())
        if mounted != installed:
            # A source-store object and its private wheel build view differ in
            # path, but share the verified selection and complete package bytes.
            module = mounted.parent.parent if mounted.parent.name == 'src' else mounted.parent
            self._cache_root(module, row['cacheSource'])
            _require(_inventory(mounted) == payload)
        relative = origin.relative_to(mounted).as_posix()
        _require(relative in payload)
        identity = {'distribution': name, 'version': row['version'], 'selection': direct,
                    'qualifiedSource': row.get('cacheSource'),
                    'packageContent': _digest(json.dumps(payload, sort_keys=True).encode())}
        self.check()
        return identity
