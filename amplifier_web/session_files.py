"""CLI-compatible paths and Context Intelligence's public JSONL disk contract.

No dependency on a host application. Foundation owns execution locking and the native history reader/writer; these
remain the existing shared session files.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re

from filelock import FileLock

_WINDOWS = os.name == 'nt'


def source_change_time(info):
    """Compare the same metadata across Windows descriptor and named stats.

    CPython 3.13 Windows fstat can expose change time as st_ctime_ns while
    stat exposes creation time there. Birth time is explicit and consistent.
    POSIX retains its change-time fence; callers still check inode/size/mtime
    and exact accepted record bytes independently.
    """
    return info.st_birthtime_ns if _WINDOWS else info.st_ctime_ns


def amplifier_home():
    return Path(os.environ.get('AMPLIFIER_HOME') or Path.home() / '.amplifier').expanduser().resolve()


# On Windows the app's own chat folders get a short project name (see project_slug).
# A separate name lets tests exercise that rule on any platform.
_SHORT_CHAT_PROJECTS = _WINDOWS
_CHAT_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
# What shows that a chat was saved under a project name: its conversation,
# its session record or its own settings. Capture and view files do not.
_HISTORY = ('transcript.jsonl', 'transcript.jsonl.backup', 'metadata.json', 'metadata.json.backup',
            'settings.yaml')


def chat_project_name(path):
    """Short project name for a path shaped ``<any>/chats/<UUID>/files``, else None.

    A string test only; it reads no file. The name comes from the chat UUID in
    the path, so it does not depend on letter case, the drive or how the path
    was resolved. A CLI name starts with '-' and this name does not, so the two
    are never equal.
    """
    path = Path(path)
    identity = path.parent.name.lower()
    if (path.name.lower() != 'files' or path.parent.parent.name.lower() != 'chats'
            or not _CHAT_ID.fullmatch(identity)):
        return None
    return 'chat-' + identity.replace('-', '')[:16]


def _unlimited(path):
    """The same absolute path in the form that Windows accepts at any length; unchanged elsewhere.

    Only for this module's own read-only checks. It is never returned to a caller.
    """
    text = str(path)
    if not _WINDOWS or text.startswith('\\\\?\\'):
        return text
    return '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text


def _present(path):
    try:
        os.lstat(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False  # Absent. Any other error is not absence and is raised.


def _has_history(project):
    """A session folder with saved history (see _HISTORY). An empty leftover is not history.

    Reads through the extended-length form, so history that was written while
    Windows long paths were on is still found after they are turned off.
    """
    if len(project.name.encode('utf-16-le')) > 510:
        return False  # Longer than any Windows folder name (255), so no such folder exists.
    try:
        with os.scandir(_unlimited(project / 'sessions')) as entries:
            return any(entry.is_dir(follow_symlinks=False) and any(
                _present(os.path.join(entry.path, name)) for name in _HISTORY) for entry in entries)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _names_folder(project, identity, folder):
    """A saved session record in this project folder names exactly this chat folder.

    The chat's own session is read first; the first record with a folder decides.
    A record that exists and cannot be read is an error, never a missing record:
    a temporary refusal must not give the same folder another project name.
    """
    sessions = project / 'sessions'
    try:
        with os.scandir(_unlimited(sessions)) as entries:
            names = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
    except (FileNotFoundError, NotADirectoryError):
        return False
    for name in sorted(names, key=lambda name: name != identity):
        for record in ('metadata.json', 'metadata.json.backup'):
            try:
                with open(_unlimited(sessions / name / record), 'rb') as stream:
                    value = json.loads(stream.read(1024 * 1024 + 1))
            except (FileNotFoundError, NotADirectoryError, ValueError):
                continue  # Absent, or not a record. Every other read error is raised.
            saved = (value.get('working_dir') or value.get('workspace')) if isinstance(value, dict) else None
            if isinstance(saved, str) and saved:
                return os.path.normcase(saved) == os.path.normcase(str(folder))
    return False


def project_slug(workspace):
    resolved = Path(workspace).expanduser().resolve()
    # Exact app-cli convention: spaces, periods and underscores are significant.
    slug = str(resolved).replace('/', '-').replace('\\', '-').replace(':', '')
    slug = slug if slug.startswith('-') else '-' + slug
    # Other platforms keep the CLI name for every folder: Foundation writes its
    # transfer fence under that name there. On Windows it only reads it.
    short = chat_project_name(resolved) if _SHORT_CHAT_PROJECTS else None
    if short is None:
        return slug  # Every folder a person chose: no file access beyond resolve().
    # A folder shaped like the app's own chat folder. Its CLI name repeats the
    # installation path inside every session path, which then exceeds the
    # Windows path limit.
    projects = amplifier_home() / 'projects'
    from .managed_chats import metadata
    # The shape and the chat ID do not prove that the app owns this folder. Its
    # allocation record does. After the chat folder is deleted, the saved
    # history must name this exact folder. A copy of a chat folder has neither.
    if not metadata(resolved) and not _names_folder(projects / short, resolved.parent.name.lower(), resolved):
        return slug
    # Existing history decides, and nothing is moved: a chat that began under
    # the CLI name stays there.
    if _has_history(projects / short):
        return short
    return slug if _has_history(projects / slug) else short


def validate_id(identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,220}', identity):
        raise ValueError('Invalid session identifier')
    return identity


def sessions_dir(workspace):
    return amplifier_home() / 'projects' / project_slug(workspace) / 'sessions'


def capture_sessions_dir(workspace):
    # Match CI's supported relocation setting; transcript paths do not relocate.
    root = Path(os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH') or amplifier_home() / 'projects').expanduser().resolve()
    return root / project_slug(workspace) / 'sessions'


def capture_dir(workspace, identity):
    return capture_sessions_dir(workspace) / validate_id(identity) / 'context-intelligence'


def validate_capture(directory):
    meta = json.loads((directory / 'metadata.json').read_text())
    if meta.get('format') != 'context-intelligence' or meta.get('version') != '1.0.0':
        raise ValueError('Unsupported Context Intelligence capture format/version; original files were preserved.')
    return meta


def append_event(workspace, identity, event, data):
    """Append a CI record once; return a small, verifiable index reference.

    One O_APPEND write keeps this compatible with the community logging hook.
    Never rewrite an events file, replay an operation, or infer remote consent.
    """
    from .host.storage import SessionStore
    directory = capture_dir(workspace, identity)
    # The name of the folder actually used: one decision for the path and the records.
    project = directory.parents[2].name
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'events.jsonl'
    record = {'event': event, 'workspace': project,
              'timestamp': data['timestamp'], 'data': data}
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with FileLock(str(directory / '.unified-append.lock')):
        metadata = directory / 'metadata.json'
        if metadata.exists():
            validate_capture(directory)
        else:
            SessionStore._atomic(metadata, json.dumps({
                'format': 'context-intelligence', 'version': '1.0.0',
                'session_id': identity, 'workspace': project,
                'working_dir': str(workspace), 'parent_id': data.get('parent_id', ''),
                'started_at': data['timestamp'], 'last_event_at': data['timestamp'], 'status': 'running',
            }))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, raw) != len(raw):
                raise OSError('Incomplete event append')
            offset = os.lseek(fd, 0, os.SEEK_CUR) - len(raw)
        finally:
            os.close(fd)
        # Existing metadata belongs to the shared hook. Do not race its lifecycle
        # updates or replace its optional fields with an app-only projection.
    return {'$event': str(path), 'offset': offset, 'bytes': len(raw), 'eventId': data.get('event_id'), 'sha256': hashlib.sha256(raw).hexdigest()}


def read_event(reference):
    path = Path(reference['$event'])
    validate_capture(path.parent)
    with path.open('rb') as stream:
        stream.seek(reference['offset'])
        raw = stream.read(reference['bytes'])
    if reference.get('sha256') and reference['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('The event index is stale; the original capture was changed.')
    row = json.loads(raw)
    if reference.get('eventId') != row.get('data', {}).get('event_id'):
        raise ValueError('The event index is stale; the original capture was changed.')
    return row


def event_index(directory):
    """Migration-only scan: make a resumed JSONL export idempotent after a crash."""
    path = directory / 'events.jsonl'
    if not path.exists():
        return {}
    validate_capture(directory)
    result = {}
    with path.open('rb') as stream:
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            row = json.loads(line)
            identity = row.get('data', {}).get('event_id')
            if identity:
                result[identity] = {'$event': str(path), 'offset': offset, 'bytes': len(line), 'eventId': identity}
    return result
