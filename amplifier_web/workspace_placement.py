"""Name-first workspace placement. Registrations describe folders, never chats."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid

from amplifier_worktrees.git import atomic
from filelock import FileLock


def defaults(service):
    configured = service.state['settings'].get('workspaces', {}).get('defaultRoot', '')
    return str(Path(configured).expanduser().resolve()) if configured else str((service.data_dir / 'workspaces').resolve())


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - {'defaultRoot', 'showPaths'}:
        raise ValueError('Unknown workspace setting.')
    if 'showPaths' in value and type(value['showPaths']) is not bool:
        raise ValueError('Show paths must be true or false.')
    if 'defaultRoot' in value:
        root = value['defaultRoot']
        if not isinstance(root, str) or len(root) > 4000 or '\0' in root:
            raise ValueError('Enter a workspace root folder.')
        if root and not Path(root).expanduser().is_absolute():
            raise ValueError('Use an absolute folder path or ~/ for the workspace root.')
        if root and Path(root).expanduser().exists() and not Path(root).expanduser().is_dir():
            raise ValueError('The workspace root must be a folder.')
    return dict(value)


def folder_name(name):
    name = unicodedata.normalize('NFKC', name).strip()
    if not name or len(name) > 200 or any(c in name for c in '/\\\0') or name in {'.', '..'}:
        raise ValueError('Enter a workspace name, without a folder path.')
    slug = re.sub(r'[^\w.-]+', '-', name.casefold(), flags=re.UNICODE).strip(' .-_')
    if not slug or len(slug.encode()) > 200 or slug.split('.')[0] in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
        raise ValueError('Choose a different workspace name.')
    return name, slug


def _directory(service):
    directory = service.data_dir / 'workspace-placement'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def _revision(service):
    return hashlib.sha256(defaults(service).encode()).hexdigest()


def _validate_created_directory(receipt):
    """A placement receipt may need recovery before the app commits registration."""
    path = Path(receipt['path'])
    try:
        info = path.lstat()
        unchanged = (stat.S_ISDIR(info.st_mode)
                     and [info.st_dev, info.st_ino] == receipt.get('directoryIdentity')
                     and path.resolve(strict=True) == path)
    except (OSError, RuntimeError):
        unchanged = False
    if not unchanged:
        raise ValueError('The created folder changed. Inspect it before attaching it again.')


def prepare(service, args):
    name, slug = folder_name(args['name'])
    raw = args.get('root') or defaults(service)
    validate_settings({'defaultRoot': raw})
    root = Path(raw).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValueError('The workspace root must be a folder.')
    path = root / slug
    # Detect filesystem-equivalent names on case-sensitive hosts too. Never
    # silently create a second spelling that collides after moving hosts.
    if root.is_dir():
        collisions = [p for p in root.iterdir() if unicodedata.normalize('NFKC', p.name).casefold() == slug]
        if collisions:
            path = collisions[0]
    if path.is_symlink():
        raise ValueError('A symbolic link uses this workspace name. Use existing folder to choose its destination explicitly.')
    registered = next((w for w in service.state['workspaces'] if w.get('path') == str(path)), None)
    disposition = 'open' if registered and path.is_dir() else 'attach' if path.is_dir() else 'blocked' if path.exists() else 'create'
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    info = ancestor.stat()
    result = {'planId': str(uuid.uuid4()), 'name': name, 'path': str(path), 'root': str(root),
              'configRevision': _revision(service), 'disposition': disposition,
              'workspaceId': registered['id'] if registered else None,
              'ancestor': str(ancestor), 'ancestorIdentity': [info.st_dev, info.st_ino]}
    atomic(_directory(service) / (result['planId'] + '.json'), result)
    return {key: value for key, value in result.items() if not key.startswith('ancestor')}


def create(service, plan_id, command_id):
    try:
        uuid.UUID(plan_id)
        plan = json.loads((_directory(service) / (plan_id + '.json')).read_text())
    except (ValueError, OSError):
        raise ValueError('This workspace plan is unavailable. Review the name again.') from None
    if not command_id:
        raise ValueError('Workspace creation requires a command ID for safe retries.')
    receipt_path = _directory(service) / ('receipt-' + hashlib.sha256(command_id.encode()).hexdigest() + '.json')
    with FileLock(str(_directory(service) / 'placement.lock')):
        # Another process may have completed this plan while we waited.
        plan = json.loads((_directory(service) / (plan_id + '.json')).read_text())
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt['planId'] != plan_id:
                raise ValueError('This command already belongs to a different workspace plan.')
            if receipt['outcome'] != 'created':
                raise ValueError('Workspace creation was interrupted. Inspect the destination and use existing folder; creation was not repeated.')
            _validate_created_directory(receipt)
            return receipt
        # A browser retry can have a fresh transport command ID after a lost
        # acknowledgment. One reviewed plan still allocates at most one folder.
        if plan.get('attempt'):
            previous = _directory(service) / plan['attempt']
            saved = json.loads(previous.read_text())
            if saved.get('outcome') != 'created':
                raise ValueError('Workspace creation was interrupted. Inspect the destination before trying again.')
            _validate_created_directory(saved)
            atomic(receipt_path, saved)
            return saved
        if plan['configRevision'] != _revision(service):
            raise ValueError('The default folder changed. Review the workspace location again.')
        if plan['disposition'] != 'create':
            raise ValueError('This folder already exists. Open its workspace or choose Use existing folder.')
        ancestor = Path(plan['ancestor'])
        info = ancestor.stat()
        if [info.st_dev, info.st_ino] != plan['ancestorIdentity'] or str(ancestor.resolve()) != str(ancestor):
            raise ValueError('The destination changed. Review the workspace location again.')
        receipt = {**plan, 'outcome': 'pending', 'receiptId': command_id}
        atomic(receipt_path, receipt)
        atomic(_directory(service) / (plan_id + '.json'), {**plan, 'attempt': receipt_path.name})
        # Pin each directory descriptor. A concurrent symlink substitution may
        # fail creation but cannot redirect it outside the reviewed root.
        fd = os.open(ancestor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(fd)
            if [opened.st_dev, opened.st_ino] != plan['ancestorIdentity']:
                raise OSError('The destination changed before creation.')
            for part in Path(plan['root']).relative_to(ancestor).parts:
                try: os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
            os.mkdir(Path(plan['path']).name, mode=0o755, dir_fd=fd)
            created = os.stat(Path(plan['path']).name, dir_fd=fd, follow_symlinks=False)
            actual = Path(plan['path']).stat()
            if Path(plan['path']).resolve() != Path(plan['path']) or (actual.st_dev, actual.st_ino) != (created.st_dev, created.st_ino):
                raise OSError('The destination moved during creation.')
            receipt['directoryIdentity'] = [created.st_dev, created.st_ino]
        except OSError as exc:
            receipt['outcome'] = 'unknown'
            atomic(receipt_path, receipt)
            raise ValueError('Could not create this workspace. Inspect the destination before trying again: ' + str(exc)) from None
        finally:
            os.close(fd)
        receipt['outcome'] = 'created'
        atomic(receipt_path, receipt)
        return receipt


def listing(service, args):
    query = args.get('query', '').strip().casefold()
    rows = [dict(row) for row in service.state['workspaces'] if row.get('available') and row.get('path')
            and (not query or query in (row['name'] + ' ' + row['path']).casefold())]
    names = {}
    for row in rows:
        names[row['name']] = names.get(row['name'], 0) + 1
    for row in rows:
        row['label'] = row['name'] + (' · ' + row['path'] if names[row['name']] > 1 else '')
    rows.sort(key=lambda row: (row['name'].casefold(), row['path']))
    offset = args.get('offset', 0)
    return {'items': rows[offset:offset + 100], 'nextOffset': offset + 100 if len(rows) > offset + 100 else None}
