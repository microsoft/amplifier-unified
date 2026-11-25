"""Bounded, review-required portability data. This module never runs task code.

Known credential shapes are rejected, never redacted. This bounded scanner cannot
recognize arbitrary secrets: a successful scan is not a confidentiality proof.
Git history/remotes/configuration are not exported; the destination repository is
provisioned independently and must already contain the exact base commit.
"""

import ast
import base64
import binascii
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import unicodedata
import zlib

from filelock import FileLock

from amplifier_worktrees.git import MAX_BYTES, MAX_FILES, common, digest, git, snapshot

MAX_CAPSULE_BYTES = 64 * 1024 * 1024
FILTER_POLICY = 'disabled; raw Git and worktree bytes'
_SECRET_KEYS = re.compile(
    r'(?:password|passwd|passphrase|secret|secretkey|accesskey|token|clientsecret|apikey|apitoken|accesstoken|'
    r'refreshtoken|idtoken|privatekey|credential|credentials|authorization|'
    r'cookie|cookies|sessiontoken|connectionstring|awsaccesskeyid|awssecretaccesskey)$'
)
_SECRET_BYTES = re.compile(
    rb'(?i)(?:\bBearer[ \t]+[A-Za-z0-9._~+/=-]{6,}|'
    rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|'
    rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|'
    rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})|'
    rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|'
    rb'\bxox[baprs]-[A-Za-z0-9-]{10,}|'
    rb'(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
    rb'password|passwd|secret[_-]?key|private[_-]?key|aws_secret_access_key)[\"\']?[ \t]*[:=][ \t]*[\"\']?[^\s\"\'{}\[\],;]{3,}|'
    rb'\b(?:https?|ssh)://[^\s/@:]+:[^\s/@]+@)'
)


def validate_bytes(data):
    """Reject known credential signatures; never silently modify public data."""
    if not isinstance(data, bytes):
        raise ValueError('Portable content must be bytes')
    if len(data) > MAX_CAPSULE_BYTES:
        raise ValueError('Portable content exceeds the capsule size limit')
    if _SECRET_BYTES.search(data):
        raise ValueError('Portable content contains a recognizable credential; review it locally')
    return data


def validate_public(value):
    """Validate JSON data and reject credential-valued fields recursively."""
    def visit(item, depth):
        if depth > 64:
            raise ValueError('Portable data is nested too deeply')
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError('Portable object keys must be strings')
                normalized = re.sub(r'[^a-z0-9]', '', key.lower())
                if (normalized == 'token' or _SECRET_KEYS.search(normalized)) and child not in (None, '', {}, []):
                    raise ValueError('Portable data contains a credential-valued field; review it locally')
                validate_bytes(key.encode('utf-8'))
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            validate_bytes(item.encode('utf-8'))
        elif item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError('Portable data must contain only JSON values')
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError('Portable numbers must be finite')
    try:
        visit(value, 0)
        if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > MAX_CAPSULE_BYTES:
            raise ValueError('Portable content exceeds the capsule size limit')
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Portable data is not valid bounded Unicode JSON') from exc
    return value


def _local_path(value):
    """Reject links in every existing component instead of resolving them away."""
    if '..' in Path(value).parts:
        raise ValueError('Unsafe local path traversal')
    path = Path(os.path.abspath(os.fspath(value)))
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Symbolic links and symbolic-link parents are not portable')
    return path


def _relative_path(value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 4096:
        raise ValueError('Unsafe portable file path')
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or '\\' in value or ':' in value
            or any(unicodedata.category(c).startswith('C') for c in value)
            or any(part in {'.', '..'} or part.rstrip(' .').lower() == '.git' for part in value.split('/'))):
        raise ValueError('Unsafe portable file path')
    if any(part.endswith((' ', '.')) for part in path.parts):
        raise ValueError('Unsafe portable file path')
    return value


def _change_path(value):
    """Host-scoped settings and credential files never travel as local changes."""
    name = _relative_path(value)
    parts = tuple(unicodedata.normalize('NFC', part).casefold() for part in PurePosixPath(name).parts)
    if '.amplifier' in parts:
        raise ValueError('Host-scoped .amplifier settings and overrides are not portable')
    filename = parts[-1]
    if (any(part in {'.ssh', '.aws', '.azure', '.gcloud'} for part in parts)
            or any(parts[index:index + 2] in {('.config', 'gcloud'), ('.config', 'gh')} for index in range(len(parts) - 1))
            or filename == '.env' or filename.startswith('.env.')
            or filename in {'.netrc', '_netrc', '.npmrc', '.pypirc', '.git-credentials', '.credentials',
                            'credentials.json', 'credentials.yaml', 'credentials.yml', 'credentials.toml',
                            'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
            or filename.endswith(('.pem', '.key', '.p12', '.pfx'))
            or parts[-2:] == ('.docker', 'config.json')):
        raise ValueError('Host credential file paths are not portable')
    return name


def _regular_bytes(path, limit=MAX_BYTES):
    path = _local_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Only regular files are portable')
        if info.st_size > limit:
            raise ValueError('Portable file exceeds the size limit')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Portable file exceeds the size limit')
        return data, stat.S_IMODE(info.st_mode)
    finally:
        os.close(fd)


def _checked_common(path):
    root, repository = common(path)
    _local_path(root); _local_path(root / '.git')
    raw_common = Path(git(root, 'rev-parse', '--git-common-dir').decode().strip())
    _local_path(raw_common if raw_common.is_absolute() else root / raw_common)
    _local_path(repository)
    keys = git(root, 'config', '--null', '--name-only', '--list').decode().split('\0')
    if any(key.lower() == 'extensions.partialclone' or key.lower().endswith('.promisor') for key in keys):
        raise ValueError('Provision complete local Git objects; partial clones can fetch implicitly')
    if os.environ.get('GIT_REPLACE_REF_BASE') or git(root, 'for-each-ref', '--format=%(refname)', 'refs/replace').strip():
        raise ValueError('Git replacement refs cannot establish the exact portable base commit')
    return root, repository


def _check_tree(repository, head):
    for entry in git(repository, 'ls-tree', '-rz', '--full-tree', head).split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode = metadata.split(b' ', 1)[0]
        if mode == b'160000':
            raise ValueError('Submodules are not portable')
        if mode not in {b'100644', b'100755'}:
            raise ValueError('Symbolic links and nonregular Git entries are not portable')
        _relative_path(name.decode('utf-8'))


def _check_source(root, head):
    _check_tree(root, head)
    if git(root, 'ls-files', '-u'):
        raise ValueError('Resolve source index conflicts before portability capture')
    for line in git(root, 'ls-files', '--debug').decode().splitlines():
        if 'flags: ' in line and int(line.rsplit('flags: ', 1)[1], 16) & 0x60008000:
            raise ValueError('Unsupported intent-to-add, skip-worktree or assume-unchanged index flags')
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode = metadata.split(b' ', 1)[0]
        if mode == b'160000':
            raise ValueError('Submodules are not portable')
        if mode not in {b'100644', b'100755'}:
            raise ValueError('Symbolic links and nonregular Git entries are not portable')
        path = _local_path(root / _relative_path(name.decode('utf-8')))
        if path.exists() and not path.is_file():
            raise ValueError('Only regular workspace files are portable')


def _decode(data):
    if not isinstance(data, str) or len(data) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError('Invalid or oversized portable base64 field')
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Invalid portable base64 field') from exc
    if base64.b64encode(raw).decode('ascii') != data:
        raise ValueError('Noncanonical portable base64 field')
    validate_bytes(raw)
    return raw


def _patch_name(name):
    if name.startswith('"'):
        try:
            decoded = ast.literal_eval(name)
            name = decoded.encode('latin-1').decode('utf-8')
        except (SyntaxError, ValueError, UnicodeError, AttributeError) as exc:
            raise ValueError('Unsafe quoted patch path') from exc
    return name


def _patch_paths(repository, patch):
    if not patch:
        return set()
    # Git parses the patch without applying it. Its output names are NUL-delimited
    # and preserve spaces; extended headers supply old paths for rename/copy.
    paths = set()
    for entry in git(repository, 'apply', '--numstat', '-z', '-', data=patch).split(b'\0'):
        if entry:
            columns = entry.split(b'\t', 2)
            if len(columns) != 3:
                raise ValueError('Unsupported portable patch structure')
            paths.add(_change_path(columns[2].decode('utf-8')))
    header = True
    for line in patch.splitlines():
        if line.startswith(b'diff --git '):
            header = True
        elif line.startswith((b'@@ ', b'GIT binary patch')):
            header = False
        if not header:
            continue
        if re.match(rb'^(?:old|new|new file|deleted file) mode ', line):
            if line.rsplit(b' ', 1)[-1] not in {b'100644', b'100755'}:
                raise ValueError('Symbolic links and submodule patches are not portable')
        for prefix in (b'rename from ', b'rename to ', b'copy from ', b'copy to '):
            if line.startswith(prefix):
                paths.add(_change_path(_patch_name(line[len(prefix):].decode('utf-8'))))
        for prefix in (b'--- ', b'+++ '):
            if line.startswith(prefix):
                name = _patch_name(line[len(prefix):].decode('utf-8').rstrip('\t'))
                if name != '/dev/null':
                    if not name.startswith(('a/', 'b/')):
                        raise ValueError('Unsupported portable patch path prefix')
                    paths.add(_change_path(name[2:]))
    if not paths:
        raise ValueError('Unsupported empty portable patch')
    return paths


def _scan_binary_patch(patch):
    """Bound and inspect the compressed literal/delta data carried by Git patches."""
    lines = iter(patch.splitlines())
    total = 0
    for line in lines:
        match = re.fullmatch(rb'(literal|delta) ([0-9]+)', line)
        if not match:
            continue
        expected = int(match[2])
        total += expected
        if total > MAX_BYTES:
            raise ValueError('Binary patch exceeds the expanded size limit')
        compressed = bytearray()
        for encoded in lines:
            if not encoded:
                break
            length = encoded[0] - 64 if 65 <= encoded[0] <= 90 else encoded[0] - 70
            if not 1 <= length <= 52 or len(encoded) - 1 != ((length + 3) // 4) * 5:
                raise ValueError('Invalid binary patch encoding')
            try:
                compressed.extend(base64.b85decode(encoded[1:])[:length])
            except ValueError as exc:
                raise ValueError('Invalid binary patch encoding') from exc
            if len(compressed) > MAX_BYTES:
                raise ValueError('Binary patch exceeds the size limit')
        try:
            decoder = zlib.decompressobj()
            expanded = decoder.decompress(compressed, expected + 1)
        except zlib.error as exc:
            raise ValueError('Invalid compressed binary patch') from exc
        if len(expanded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Binary patch exceeds its declared size or is incomplete')
        if match[1] == b'delta':
            # Delta instructions may be tiny while requesting an enormous result.
            # Bound both sizes before Git can materialize that result on disk.
            cursor, sizes = 0, []
            for _ in range(2):
                size, shift = 0, 0
                while True:
                    if cursor >= len(expanded) or shift > 63:
                        raise ValueError('Invalid binary delta size')
                    byte = expanded[cursor]; cursor += 1
                    size |= (byte & 0x7f) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                sizes.append(size)
            total += max(sizes) - expected if max(sizes) > expected else 0
            if total > MAX_BYTES:
                raise ValueError('Binary delta exceeds the expanded size limit')
        validate_bytes(expanded)
    return total


def _scan_changed_files(root, head, patches):
    """Inspect actual changed bytes, including binary content compressed by Git."""
    changed = set().union(*(_patch_paths(root, patch) for patch in patches))
    objects = set()
    for entry in git(root, 'ls-tree', '-rz', '--full-tree', head).split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            if name.decode('utf-8') in changed:
                objects.add(metadata.split()[2].decode('ascii'))
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            if name.decode('utf-8') in changed:
                objects.add(metadata.split()[1].decode('ascii'))
    for identity in objects:
        size = int(git(root, 'cat-file', '-s', identity))
        if size > MAX_BYTES:
            raise ValueError('Changed Git blob exceeds the portable size limit')
        validate_bytes(git(root, 'cat-file', 'blob', identity))
    for name in changed:
        path = _local_path(root / name)
        if path.exists():
            raw, _ = _regular_bytes(path)
            validate_bytes(raw)


def _workspace_payload(payload, repository=None):
    validate_public(payload)
    required = {'schemaVersion', 'head', 'sourceRevision', 'mode', 'stagedPatch', 'unstagedPatch',
                'untracked', 'ignoredFilesIncluded', 'filterPolicy', 'reviewRequired'}
    if (not isinstance(payload, dict) or set(payload) != required
            or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1):
        raise ValueError('Unsupported portable workspace schema')
    if (not isinstance(payload['head'], str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', payload['head'])
            or not isinstance(payload['sourceRevision'], str) or not re.fullmatch(r'[a-f0-9]{64}', payload['sourceRevision'])):
        raise ValueError('Invalid portable commit or source revision')
    if (not isinstance(payload['mode'], str) or payload['mode'] not in {'clean', 'carry_dirty'} or payload['ignoredFilesIncluded'] is not False
            or payload['reviewRequired'] is not True or payload['filterPolicy'] != FILTER_POLICY):
        raise ValueError('Unsupported portable workspace policy')
    patches = [_decode(payload['stagedPatch']), _decode(payload['unstagedPatch'])]
    expanded_bytes = sum(_scan_binary_patch(patch) for patch in patches)
    if not isinstance(payload['untracked'], list) or len(payload['untracked']) > MAX_FILES:
        raise ValueError('Too many portable files')
    total, names, contents = sum(map(len, patches)), set(), []
    for row in payload['untracked']:
        if not isinstance(row, dict) or set(row) != {'path', 'data', 'sha256', 'bytes', 'mode'}:
            raise ValueError('Invalid portable file record')
        name = _change_path(row['path'])
        # Refuse ambiguous aliases across case-sensitive and case-insensitive hosts.
        key = unicodedata.normalize('NFC', name).casefold()
        if key in names:
            raise ValueError('Duplicate or ambiguous portable file path')
        names.add(key)
        data = _decode(row['data'])
        if (type(row['bytes']) is not int or row['bytes'] != len(data) or row['sha256'] != digest(data)
                or type(row['mode']) is not int or not 0 <= row['mode'] <= 0o777):
            raise ValueError('Portable file integrity or mode is invalid')
        total += len(data)
        expanded_bytes += len(data)
        contents.append(data)
    if any(any(str(parent) in names for parent in PurePosixPath(name).parents) for name in names):
        raise ValueError('Overlapping portable file paths')
    if total > MAX_BYTES or expanded_bytes > MAX_BYTES:
        raise ValueError('Portable workspace exceeds the aggregate size limit')
    if payload['mode'] == 'clean' and (any(patches) or contents):
        raise ValueError('Clean portability cannot include local changes')
    if repository is not None:
        changed = set().union(*(_patch_paths(repository, patch) for patch in patches))
        if len(changed | {row['path'] for row in payload['untracked']}) > MAX_FILES:
            raise ValueError('Too many changed portable files')
    return patches, contents


def capture_workspace(path, expected_revision, mode='clean'):
    """Capture only reviewed local changes, binding capture to a stable revision."""
    path = _local_path(path)
    root, repository = _checked_common(path)
    if path != root:
        raise ValueError('Capture requires the repository worktree root')
    if mode not in {'clean', 'carry_dirty'}:
        raise ValueError('Choose clean or carry_dirty explicitly')
    with FileLock(str(repository / 'amplifier-managed-worktrees.lock'), timeout=30):
        head = git(root, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        _check_source(root, head)
        before, staged, unstaged = snapshot(root)
        if before['sourceRevision'] != expected_revision:
            raise ValueError('The source checkout changed; inspect it again')
        if mode == 'clean' and before['dirty']:
            raise ValueError('Clean portability requires a clean source; choose carry_dirty explicitly')
        files = []
        for row in before['untracked']:
            name = _change_path(row['path'])
            raw, file_mode = _regular_bytes(root / name)
            if digest(raw) != row['sha256'] or file_mode != row['mode']:
                raise ValueError('The source checkout changed during capture')
            validate_bytes(raw)
            files.append({'path': name, 'data': base64.b64encode(raw).decode('ascii'),
                          'sha256': digest(raw), 'bytes': len(raw), 'mode': file_mode})
        payload = {'schemaVersion': 1, 'head': before['head'], 'sourceRevision': before['sourceRevision'],
                   'mode': mode, 'stagedPatch': base64.b64encode(staged).decode('ascii'),
                   'unstagedPatch': base64.b64encode(unstaged).decode('ascii'), 'untracked': files,
                   'ignoredFilesIncluded': False, 'filterPolicy': FILTER_POLICY, 'reviewRequired': True}
        _workspace_payload(payload, root)
        _scan_changed_files(root, before['head'], [staged, unstaged])
        if snapshot(root)[0]['sourceRevision'] != before['sourceRevision']:
            raise ValueError('The source checkout changed during capture')
        return payload


def restore_workspace(payload, repository, target):
    """Create a fresh detached worktree; failures preserve any partial destination."""
    repository, target = _local_path(repository), _local_path(target)
    root, git_directory = _checked_common(repository)
    patches, contents = _workspace_payload(payload, root)
    if target.exists() or target.is_symlink():
        raise ValueError('Portable destination must be a fresh, absent path')
    if target.is_relative_to(root) or target.is_relative_to(git_directory):
        raise ValueError('Portable destination must be outside the provisioned repository')
    with FileLock(str(git_directory / 'amplifier-managed-worktrees.lock'), timeout=30):
        head = git(root, 'rev-parse', '--verify', '--end-of-options', payload['head'] + '^{commit}').decode().strip()
        if head != payload['head']:
            raise ValueError('The exact portable base commit is missing at the destination')
        _check_tree(root, head)
        # Check all paths before Git creates files. Git also enforces its own
        # traversal protections during apply; --unsafe-paths is never enabled.
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _local_path(target)
        if target.exists():
            raise ValueError('Portable destination must be a fresh, absent path')
        target.mkdir(mode=0o700)
        git(root, 'worktree', 'add', '--detach', str(target), head)
        if patches[0]:
            git(target, 'apply', '--check', '--index', '--binary', '-', data=patches[0])
            git(target, 'apply', '--index', '--binary', '-', data=patches[0])
        if patches[1]:
            git(target, 'apply', '--check', '--binary', '-', data=patches[1])
            git(target, 'apply', '--binary', '-', data=patches[1])
        for row, data in zip(payload['untracked'], contents):
            destination = _local_path(target / row['path'])
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, row['mode'])
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), row['mode'])
        after = snapshot(target)[0]
        _check_source(target, head)
        _scan_changed_files(target, head, patches)
        if after['head'] != head or after['sourceRevision'] != payload['sourceRevision']:
            raise ValueError('Destination revision differs; the partial checkout is preserved for review')
        return {'workingDirectory': str(target), 'sourceRevision': after['sourceRevision'], 'head': head,
                'owned': True, 'reviewRequired': True, 'inputsReplayed': False}


def write_capsule(path, payload):
    """Write private atomic JSON after validation; existing symlinks are refused."""
    validate_public(payload)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_CAPSULE_BYTES:
        raise ValueError('Portable content exceeds the capsule size limit')
    path = _local_path(path)
    from .protocol import durable_directory
    durable_directory(path.parent)
    _local_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.capsule-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        _local_path(path)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'sha256': digest(encoded), 'bytes': len(encoded)}


def read_capsule(path):
    """Read bounded regular JSON, rejecting duplicate keys and recognizable secrets."""
    raw, _ = _regular_bytes(path, MAX_CAPSULE_BYTES)
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError('Duplicate portable JSON key')
            value[key] = child
        return value
    try:
        payload = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('Invalid capsule JSON') from exc
    return validate_public(payload)
