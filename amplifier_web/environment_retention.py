"""Bounded archive-only retention; guest paths are never extracted on the host."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path

INPUT_LIMIT = 8 * 1024 ** 3
SELECTED_LIMIT = 512 * 1024 ** 2
RECOVERY_LIMIT = 4 * 1024 ** 3
MEMBER_LIMIT = 200000
PREFIX = 'backup/container/rootfs/'
SELECTION = ('workspace', 'artifacts')

VALIDATION_REASONS = frozenset({
    'invalid_header', 'metadata_limit', 'name_limit', 'sparse_entry',
    'missing_terminator', 'trailing_data', 'incomplete_footer', 'unsafe_path',
    'archive_type', 'archive_limit', 'member_limit', 'selection_mismatch',
    'selected_size_limit', 'missing_root', 'duplicate_entry', 'invalid_size',
    'unsupported_type', 'ancestor_traversal', 'ancestor_replaced',
    'symbolic_target', 'hard_target', 'symbolic_cycle', 'receipt_mismatch',
    'archive_format',
})
MEMBER_TYPES = {tarfile.REGTYPE: 'file', tarfile.AREGTYPE: 'file',
                tarfile.DIRTYPE: 'directory', tarfile.SYMTYPE: 'symbolic',
                tarfile.LNKTYPE: 'hard', tarfile.FIFOTYPE: 'fifo',
                tarfile.CHRTYPE: 'character', tarfile.BLKTYPE: 'block',
                tarfile.GNUTYPE_SPARSE: 'sparse'}


def validation_diagnostic(value):
    """Allowlist retained metadata again at its reader; never echo arbitrary data."""
    if (not isinstance(value, dict) or set(value) - {'version', 'reasonCode', 'memberType', 'relativePath'}
            or type(value.get('version')) is not int or value['version'] != 1
            or not isinstance(value.get('reasonCode'), str) or value['reasonCode'] not in VALIDATION_REASONS):
        return None
    result = {'version': 1, 'reasonCode': value['reasonCode']}
    kind = value.get('memberType')
    if isinstance(kind, str) and kind in {*MEMBER_TYPES.values(), 'other'}:
        result['memberType'] = kind
    # Omit unsafe/long names rather than truncating or normalizing them into a
    # different valid path. Never pass a link target or host destination here.
    path = value.get('relativePath')
    if (isinstance(path, str) and 0 < len(path) <= 256
            and re.fullmatch(r'(workspace|artifacts)(/[A-Za-z0-9_.-]+)*', path)
            and all(part not in {'.', '..'} for part in path.split('/'))):
        result['relativePath'] = path
    return result


class ValidationFailure(ValueError):
    """Same rejection semantics, with a bounded, non-authorizing explanation."""
    def __init__(self, reason, message, *, item=None, name=None):
        super().__init__(message)
        self.diagnostic = validation_diagnostic({
            'version': 1, 'reasonCode': reason,
            **({'memberType': MEMBER_TYPES.get(item.type, 'other')} if item is not None else {}),
            **({'relativePath': name} if name is not None else {}),
        })


def validation_failure_diagnostic(error):
    if isinstance(error, ValidationFailure):
        return validation_diagnostic(error.diagnostic)
    if isinstance(error, tarfile.TarError):
        return {'version': 1, 'reasonCode': 'archive_format'}
    return None


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
            raise ValidationFailure('invalid_header', 'The archive contains an invalid or incomplete header.') from exc

    def _proc_pax(self, archive):
        if self.size > 1024 * 1024:
            raise ValidationFailure('metadata_limit', 'Archive metadata exceeds its bound.')
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        if self.size > 4096:
            raise ValidationFailure('name_limit', 'An archive name exceeds its bound.')
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, archive):
        raise ValidationFailure('sparse_entry', 'Sparse archive metadata is unsupported.')

    def _proc_gnusparse_00(self, *args):
        raise ValidationFailure('sparse_entry', 'Sparse archive metadata is unsupported.')

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


def verified_footer(archive):
    if not getattr(archive, 'verified_zero_termination', False):
        raise ValidationFailure('missing_terminator', 'The archive has no verified terminator.')
    remaining = 0
    # Read through TarFile's stream wrapper: it may already hold read-ahead
    # bytes that are no longer visible on the original subprocess pipe.
    while block := archive.fileobj.read(65536):
        remaining += len(block)
        if any(block):
            raise ValidationFailure('trailing_data', 'The archive has nonzero data after its terminator.')
    if remaining < 512 or remaining % 512:
        raise ValidationFailure('incomplete_footer', 'The archive is missing its complete footer.')


def canonical(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value or value.startswith('/'):
        raise ValidationFailure('unsafe_path', 'An archive path is unsafe.')
    parts = value.rstrip('/').split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ValidationFailure('unsafe_path', 'An archive path is not canonical.')
    return '/'.join(parts)


def digest(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    checksum, size = hashlib.sha256(), 0
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValidationFailure('archive_type', 'A retained archive is not a regular file.')
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > INPUT_LIMIT:
                raise ValidationFailure('archive_limit', 'A retained archive exceeds its bound.')
            checksum.update(block)
        os.fsync(stream.fileno())
    return checksum.hexdigest(), size


def inspect_archive(path, expected_root, *, archive_only=False):
    """Validate an independently exported selection before destructive removal."""
    count, logical, names, links, ancestors = 0, 0, {}, {}, set()
    with tarfile.open(path, 'r:', tarinfo=BoundedTarInfo, stream=True) as archive:
        for item in archive:
            count += 1
            if count > MEMBER_LIMIT:
                raise ValidationFailure('member_limit', 'The retained selection has too many entries.')
            name = canonical(item.name)
            if name.split('/')[0] != expected_root:
                raise ValidationFailure('selection_mismatch', 'An archive contains files outside its declared selection.')
            validate_member(item, name, names, links, ancestors, archive_only=archive_only)
            logical += item.size if item.isfile() else 0
            if logical > (RECOVERY_LIMIT if archive_only else SELECTED_LIMIT):
                raise ValidationFailure('selected_size_limit', 'The retained selection exceeds its bound.', item=item, name=name)
        verified_footer(archive)
    if not archive_only:
        validate_links(names, links)
    if names.get(expected_root) != 'directory':
        raise ValidationFailure('missing_root', 'The selected root directory was not retained.')
    return logical


def validate_member(item, name, names, links, ancestors, *, archive_only=False):
    inert_fifo = archive_only and item.type == tarfile.FIFOTYPE
    reason = ('duplicate_entry' if name in names else 'invalid_size' if item.size < 0
              else 'sparse_entry' if item.sparse is not None or any(key.startswith('GNU.sparse') for key in item.pax_headers)
              else 'invalid_size' if inert_fifo and item.size != 0
              else 'unsupported_type' if not (item.isfile() or item.isdir() or item.issym() or item.islnk() or inert_fifo) else None)
    if reason:
        raise ValidationFailure(reason, 'The selected filesystem contains unsupported or duplicate entries.', item=item, name=name)
    parts = name.split('/')
    parents = ['/'.join(parts[:i]) for i in range(1, len(parts))]
    if any(names.get(parent) in {'symbolic', 'file', 'hard', 'fifo'} for parent in parents):
        raise ValidationFailure('ancestor_traversal', 'An archive entry traverses another file or link.', item=item, name=name)
    kind = 'file' if item.isfile() else 'directory' if item.isdir() else 'symbolic' if item.issym() else 'fifo' if inert_fifo else 'hard'
    if kind != 'directory' and name in ancestors:
        raise ValidationFailure('ancestor_replaced', 'An archive link replaces a selected directory.', item=item, name=name)
    if item.issym():
        target = item.linkname
        if not target or len(target) > 4096 or '\x00' in target or (not archive_only and target.startswith('/')):
            raise ValidationFailure('symbolic_target', 'An archive symbolic link leaves the retained selection.', item=item, name=name)
        links[name] = target
    elif item.islnk():
        target = canonical(item.linkname)
        if names.get(target) != 'file':
            raise ValidationFailure('hard_target', 'A hard link does not target an earlier retained regular file.', item=item, name=name)
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
                    raise ValidationFailure('symbolic_target', 'A symbolic link escapes its retained root.', name=name)
                resolved.pop()
                continue
            resolved.append(part)
            link = '/'.join(resolved)
            if link in links:
                if link in visited or len(visited) >= 64:
                    raise ValidationFailure('symbolic_cycle', 'A symbolic link cycle cannot be verified.', name=name)
                visited.add(link)
                resolved.pop()
                pending = links[link].split('/') + pending
        if not resolved or resolved[0] != name.split('/')[0]:
            raise ValidationFailure('symbolic_target', 'A symbolic link leaves its retained root.', name=name)


def filter_incus_stream(stream, destination, identity, *, selections=SELECTION, archive_only=False):
    """Filter a bounded backup without extracting guest-controlled paths."""
    if selections not in (SELECTION, ('artifacts',)):
        raise ValueError('Unsupported retained selection.')
    destination = Path(destination)
    outputs, files, names, links, ancestors, roots = {}, {}, {}, {}, {}, set()
    count = logical = 0
    try:
        for selection in selections:
            files[selection] = (destination / (selection + '.tar')).open('xb')
            outputs[selection] = tarfile.open(fileobj=files[selection], mode='w', format=tarfile.PAX_FORMAT)  # noqa: SIM115 - closed before fsync in finally.
            names[selection], links[selection] = {}, {}
            ancestors[selection] = set()
        with tarfile.open(fileobj=stream, mode='r|', tarinfo=BoundedTarInfo, stream=True) as archive:
            for item in archive:
                count += 1
                if count > MEMBER_LIMIT:
                    raise ValidationFailure('member_limit', 'The instance export has too many entries.')
                full = canonical(item.name)
                if not full.startswith(PREFIX):
                    continue
                relative = full[len(PREFIX):]
                selection = relative.split('/')[0]
                if selection not in selections:
                    continue
                selected = copy.copy(item)
                selected.name = relative
                selected.pax_headers = dict(item.pax_headers)
                selected.pax_headers.pop('path', None)
                if selected.islnk():
                    target = canonical(selected.linkname)
                    if not target.startswith(PREFIX + selection + '/'):
                        raise ValidationFailure('hard_target', 'A hard link leaves its selected filesystem.', item=selected, name=relative)
                    selected.linkname = target[len(PREFIX):]
                    selected.pax_headers.pop('linkpath', None)
                if relative == selection:
                    if not item.isdir():
                        raise ValidationFailure('missing_root', 'A selected root is not a directory.', item=selected, name=relative)
                    roots.add(selection)
                validate_member(selected, relative, names[selection], links[selection], ancestors[selection], archive_only=archive_only)
                logical += selected.size if selected.isfile() else 0
                if logical > (RECOVERY_LIMIT if archive_only else SELECTED_LIMIT):
                    raise ValidationFailure('selected_size_limit', 'The selected filesystem exceeds its retention limit.', item=selected, name=relative)
                outputs[selection].addfile(selected, archive.extractfile(item) if item.isfile() else None)
                # Manual addfile writes and close use the byte offset, never the
                # prior-member cache. Link validation has its own bounded index.
                outputs[selection].members.clear()
            verified_footer(archive)
        if roots != set(selections):
            raise ValidationFailure('missing_root', 'The instance export does not contain all selected roots.')
        if not archive_only:
            for selection in selections:
                validate_links(names[selection], links[selection])
    finally:
        for output in outputs.values():
            output.close()
        for output in files.values():
            output.flush()
            os.fsync(output.fileno())
            output.close()
    return [{'id': identity, 'source': '/' + selection, 'destination': str(destination / (selection + '.tar'))}
            for selection in selections]


def verify_artifacts(archives):
    """Artifacts-only evidence is never a removal or workspace-retention grant."""
    if len(archives) != 1 or archives[0].get('source') != '/artifacts':
        raise ValueError('Exactly the artifacts filesystem must be retained.')
    archive = archives[0]
    path = Path(archive['destination'])
    checksum, size = digest(path)
    if archive.get('sha256') not in {None, checksum} or archive.get('bytes') not in {None, size}:
        raise ValidationFailure('receipt_mismatch', 'The exported archive does not match its receipt.')
    logical = inspect_archive(path, 'artifacts')
    archive.update(bytes=size, sha256=checksum, logicalBytes=logical, verified=True)
    result = {'archiveBytes': size, 'logicalBytes': logical, 'format': 'tar',
              'selection': ['/artifacts'], 'verified': True}
    with (path.parent / 'artifacts-retention.json').open('x') as output:
        json.dump(result, output)
        output.flush()
        os.fsync(output.fileno())
    return result


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
    if mode in {'files', 'archive-only'} and (len(archives) != 2 or {row.get('source') for row in archives} != {'/workspace', '/artifacts'}):
        raise ValidationFailure('selection_mismatch', 'Both distinct selected filesystems must be retained.')
    if len({row.get('destination') for row in archives}) != len(archives):
        raise ValidationFailure('selection_mismatch', 'Retained archives cannot share a destination.')
    total = 0
    for archive in archives:
        path = Path(archive['destination'])
        checksum, size = digest(path)
        if archive.get('sha256') not in {None, checksum} or archive.get('bytes') not in {None, size}:
            raise ValidationFailure('receipt_mismatch', 'The exported archive does not match its receipt.')
        if mode in {'files', 'archive-only'}:
            archive['logicalBytes'] = inspect_archive(path, archive['source'].strip('/'), archive_only=mode == 'archive-only')
        archive.update(bytes=size, sha256=checksum, verified=True)
        total += size
    source_hash, source_size = digest(Path(root) / 'source.tar')
    result = {'archiveBytes': total, 'sourceBytes': source_size, 'totalBytes': total + source_size,
              'format': 'tar', 'policy': mode, 'verified': True,
              'selection': ['/workspace', '/artifacts'] if mode in {'files', 'archive-only'} else ['/'],
              'sourceSha256': source_hash}
    # Existing ordinary receipts have no archiveOnly field. Passive verification
    # must preserve that exact historical shape without rewriting old evidence.
    if mode == 'archive-only':
        result['archiveOnly'] = True
    if publish:
        publish_record(Path(archives[0]['destination']).parent / 'retention.json', result)
    return result
