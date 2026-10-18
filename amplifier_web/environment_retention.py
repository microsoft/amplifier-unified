"""Bounded archive-only retention; guest paths are never extracted on the host."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tarfile
import tempfile
from pathlib import Path

INPUT_LIMIT = 8 * 1024 ** 3
SELECTED_LIMIT = 512 * 1024 ** 2
MEMBER_LIMIT = 200000
PREFIX = 'backup/container/rootfs/'
SELECTION = ('workspace', 'artifacts')


class BoundedTarInfo(tarfile.TarInfo):
    @classmethod
    def fromtarfile(cls, archive):
        try:
            return super().fromtarfile(archive)
        except tarfile.EOFHeaderError:
            archive.verified_zero_termination = True
            raise
        except tarfile.HeaderError as exc:
            # TarFile.next normally treats a corrupt later header as EOF.
            # Preservation must fail instead of silently dropping later files.
            raise ValueError('The archive contains an invalid or incomplete header.') from exc

    def _proc_pax(self, archive):
        if self.size > 1024 * 1024:
            raise ValueError('Archive metadata exceeds its bound.')
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        if self.size > 4096:
            raise ValueError('An archive name exceeds its bound.')
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, archive):
        raise ValueError('Sparse archive metadata is unsupported.')

    def _proc_gnusparse_00(self, *args):
        raise ValueError('Sparse archive metadata is unsupported.')

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


def verified_footer(archive):
    if not getattr(archive, 'verified_zero_termination', False):
        raise ValueError('The archive has no verified terminator.')
    remaining = 0
    # Read through TarFile's stream wrapper: it may already hold read-ahead
    # bytes that are no longer visible on the original subprocess pipe.
    while block := archive.fileobj.read(65536):
        remaining += len(block)
        if any(block):
            raise ValueError('The archive has nonzero data after its terminator.')
    if remaining < 512 or remaining % 512:
        raise ValueError('The archive is missing its complete footer.')


def canonical(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value or value.startswith('/'):
        raise ValueError('An archive path is unsafe.')
    parts = value.rstrip('/').split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ValueError('An archive path is not canonical.')
    return '/'.join(parts)


def digest(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    checksum, size = hashlib.sha256(), 0
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('A retained archive is not a regular file.')
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > INPUT_LIMIT:
                raise ValueError('A retained archive exceeds its bound.')
            checksum.update(block)
        os.fsync(stream.fileno())
    return checksum.hexdigest(), size


def inspect_archive(path, expected_root):
    """Validate an independently exported selection before destructive removal."""
    count, logical, names, links, ancestors = 0, 0, {}, {}, set()
    with tarfile.open(path, 'r:', tarinfo=BoundedTarInfo, stream=True) as archive:
        for item in archive:
            count += 1
            if count > MEMBER_LIMIT:
                raise ValueError('The retained selection has too many entries.')
            name = canonical(item.name)
            if name.split('/')[0] != expected_root:
                raise ValueError('An archive contains files outside its declared selection.')
            validate_member(item, name, names, links, ancestors)
            logical += item.size if item.isfile() else 0
            if logical > SELECTED_LIMIT:
                raise ValueError('The retained selection exceeds its bound.')
        verified_footer(archive)
    validate_links(names, links)
    if names.get(expected_root) != 'directory':
        raise ValueError('The selected root directory was not retained.')
    return logical


def validate_member(item, name, names, links, ancestors):
    if (name in names or item.size < 0 or item.sparse is not None
            or any(key.startswith('GNU.sparse') for key in item.pax_headers)
            or not (item.isfile() or item.isdir() or item.issym() or item.islnk())):
        raise ValueError('The selected filesystem contains unsupported or duplicate entries.')
    parts = name.split('/')
    parents = ['/'.join(parts[:i]) for i in range(1, len(parts))]
    if any(names.get(parent) in {'symbolic', 'file', 'hard'} for parent in parents):
        raise ValueError('An archive entry traverses another file or link.')
    kind = 'file' if item.isfile() else 'directory' if item.isdir() else 'symbolic' if item.issym() else 'hard'
    if kind != 'directory' and name in ancestors:
        raise ValueError('An archive link replaces a selected directory.')
    if item.issym():
        target = item.linkname
        if not target or len(target) > 4096 or target.startswith('/') or '\x00' in target:
            raise ValueError('An archive symbolic link leaves the retained selection.')
        links[name] = target
    elif item.islnk():
        target = canonical(item.linkname)
        if names.get(target) != 'file':
            raise ValueError('A hard link does not target an earlier retained regular file.')
    names[name] = kind
    ancestors.update(parents)


def validate_links(names, links):
    for name, target in links.items():
        pending = name.split('/')[:-1] + target.split('/')
        resolved, visited = [], set()
        while pending:
            part = pending.pop(0)
            if part in {'', '.'}:
                continue
            if part == '..':
                if len(resolved) <= 1:
                    raise ValueError('A symbolic link escapes its retained root.')
                resolved.pop()
                continue
            resolved.append(part)
            link = '/'.join(resolved)
            if link in links:
                if link in visited or len(visited) >= 64:
                    raise ValueError('A symbolic link cycle cannot be verified.')
                visited.add(link)
                resolved.pop()
                pending = links[link].split('/') + pending
        if not resolved or resolved[0] != name.split('/')[0]:
            raise ValueError('A symbolic link leaves its retained root.')


def filter_incus_stream(stream, destination, identity):
    """Read one complete plain Incus backup, retaining two verified tar files."""
    destination = Path(destination)
    outputs, files, names, links, ancestors, roots = {}, {}, {}, {}, {}, set()
    count = logical = 0
    try:
        for selection in SELECTION:
            files[selection] = (destination / (selection + '.tar')).open('xb')
            outputs[selection] = tarfile.open(fileobj=files[selection], mode='w', format=tarfile.PAX_FORMAT)  # noqa: SIM115 - closed before fsync in finally.
            names[selection], links[selection] = {}, {}
            ancestors[selection] = set()
        with tarfile.open(fileobj=stream, mode='r|', tarinfo=BoundedTarInfo, stream=True) as archive:
            for item in archive:
                count += 1
                if count > MEMBER_LIMIT:
                    raise ValueError('The instance export has too many entries.')
                full = canonical(item.name)
                if not full.startswith(PREFIX):
                    continue
                relative = full[len(PREFIX):]
                selection = relative.split('/')[0]
                if selection not in SELECTION:
                    continue
                selected = copy.copy(item)
                selected.name = relative
                selected.pax_headers = dict(item.pax_headers)
                selected.pax_headers.pop('path', None)
                if selected.islnk():
                    target = canonical(selected.linkname)
                    if not target.startswith(PREFIX + selection + '/'):
                        raise ValueError('A hard link leaves its selected filesystem.')
                    selected.linkname = target[len(PREFIX):]
                    selected.pax_headers.pop('linkpath', None)
                if relative == selection:
                    if not item.isdir():
                        raise ValueError('A selected root is not a directory.')
                    roots.add(selection)
                validate_member(selected, relative, names[selection], links[selection], ancestors[selection])
                logical += selected.size if selected.isfile() else 0
                if logical > SELECTED_LIMIT:
                    raise ValueError('The selected filesystem exceeds its retention limit.')
                outputs[selection].addfile(selected, archive.extractfile(item) if item.isfile() else None)
                # Manual addfile writes and close use the byte offset, never the
                # prior-member cache. Link validation has its own bounded index.
                outputs[selection].members.clear()
            verified_footer(archive)
        if roots != set(SELECTION):
            raise ValueError('The instance export does not contain both selected roots.')
        for selection in SELECTION:
            validate_links(names[selection], links[selection])
    finally:
        for output in outputs.values():
            output.close()
        for output in files.values():
            output.flush()
            os.fsync(output.fileno())
            output.close()
    return [{'id': identity, 'source': '/' + selection, 'destination': str(destination / (selection + '.tar'))}
            for selection in SELECTION]


def publish_record(path, value):
    """Publish one complete private JSON record; never replace earlier evidence."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.retention-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        Path(temporary).unlink()
        for directory in (path.parent, path.parent.parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def verify_retention(root, archives, mode='files', *, publish=True):
    """Persist byte-verified selection receipts; this never scans the live guest."""
    if not publish:
        # Observation must not change caller-owned records or existing receipts.
        archives = copy.deepcopy(archives)
    if mode == 'files' and (len(archives) != 2 or {row.get('source') for row in archives} != {'/workspace', '/artifacts'}):
        raise ValueError('Both distinct selected filesystems must be retained.')
    if len({row.get('destination') for row in archives}) != len(archives):
        raise ValueError('Retained archives cannot share a destination.')
    total = 0
    for archive in archives:
        path = Path(archive['destination'])
        checksum, size = digest(path)
        if archive.get('sha256') not in {None, checksum} or archive.get('bytes') not in {None, size}:
            raise ValueError('The exported archive does not match its receipt.')
        if mode == 'files':
            archive['logicalBytes'] = inspect_archive(path, archive['source'].strip('/'))
        archive.update(bytes=size, sha256=checksum, verified=True)
        total += size
    source_hash, source_size = digest(Path(root) / 'source.tar')
    result = {'archiveBytes': total, 'sourceBytes': source_size, 'totalBytes': total + source_size,
              'format': 'tar', 'policy': mode, 'verified': True,
              'selection': ['/workspace', '/artifacts'] if mode == 'files' else ['/'],
              'sourceSha256': source_hash}
    if publish:
        publish_record(Path(archives[0]['destination']).parent / 'retention.json', result)
    return result
