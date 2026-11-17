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


# On Windows the app's own folders get a short project name (see project_slug):
# a chat folder, and every folder in a check that runs in an update stage.
# A separate name lets tests exercise that rule on any platform.
_SHORT_CHAT_PROJECTS = _WINDOWS
_CHAT_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_STAGE_ID = re.compile(r'[0-9a-f]{32}')
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


def stage_project_name():
    """Short project name for every folder, when this process runs in an update stage, else None.

    A stage is ``<app home>/updates/releases/<32 hex>``. The app checks new
    components there, and the setup of a chat that has its own (dependency
    overrides). The check is a child process with the stage as its app home
    (``AMPLIFIER_WEB_HOME``) and ``<stage>/shared-config`` as its shared home.
    Its workspace is the update's ``<stage>/qualification-workspace``, or the
    folder of the chat: an app chat folder, or a folder that a person chose.

    The session of a check is not a saved conversation. Its history is in the
    stage's own shared home, where no chat and no person has history to keep.
    A CLI name there repeats the path of the workspace below the stage, so the
    session paths pass the Windows path limit. So every folder gets one name
    in such a process. The name comes from the stage ID in the app home
    setting; no file is read. It starts with neither '-' nor 'chat-', so
    it never equals a CLI name or a chat name. Each stage has its own shared
    home, so the session folders of two stages never meet.

    The shared home must be that stage's own ``shared-config`` too. So the
    name is never used where a person's history is: a process with a stage as
    its app home and another shared home keeps every name.

    In every other process each folder keeps its name: the app and a person's
    own tools never run with a stage as their app home.
    """
    home, shared = os.environ.get('AMPLIFIER_WEB_HOME'), os.environ.get('AMPLIFIER_HOME')
    if not home or not shared:
        return None
    stage = Path(home)
    identity = stage.name.lower()
    if (not _STAGE_ID.fullmatch(identity) or stage.parent.name.lower() != 'releases'
            or stage.parent.parent.name.lower() != 'updates'):
        return None
    # Names only: both settings come from the code that made the stage.
    if os.path.normcase(os.path.abspath(shared)) != os.path.normcase(os.path.abspath(stage / 'shared-config')):
        return None
    return 'update-' + identity[:12]


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
    if not _SHORT_CHAT_PROJECTS:
        return slug
    # A check in an update stage has no history to keep: each check is a new
    # session in the stage's own shared home, whatever its workspace is.
    staged = stage_project_name()
    if staged is not None:
        return staged
    short = chat_project_name(resolved)
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


# What the app must be able to write to keep a chat, after the session ID: the
# longest name below the session folder (the temporary file of the saved view)
# and below the capture folder (the capture hook's temporary file).
# tests/test_windows_path_budget.py holds both against the rows of the budget.
_FILE_LIMIT = 259
_SESSION_TAIL = len('\\unified\\.presentation-' + 'f' * 32 + '.tmp')
_CAPTURE_TAIL = len('\\context-intelligence\\metadata.json.' + '9' * 10 + '.tmp')


def windows_refuses_long_paths():
    """True when this PC has Windows long paths off, which is the Windows default. False when it is not known."""
    if not _WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return winreg.QueryValueEx(key, 'LongPathsEnabled')[0] != 1
    except FileNotFoundError:
        return True   # The value is absent: the Windows default.
    except OSError:
        return False  # Not known. Windows decides at the write, as before.


def chat_files_identity(session):
    """The ID under which the files of this chat are saved: the one that its conversation worker runs with."""
    return session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']


def chat_folder_too_long(workspace, identity):
    """Why Windows cannot keep a chat for a folder that a person chose, in plain words, else None.

    Such a folder keeps the CLI project name, which is as long as the folder's
    own path, so a long path puts the chat's saved files over the Windows path
    limit. Without this answer the app cannot create the chat ("unexpected
    response (500)"), or the conversation worker stops at its first write.

    The measure is what keeping a chat needs: its history, its saved view and
    its capture. A background-job record and the request diagnostics have
    longer names; a folder that fits here can still lose those (the design
    document states the numbers). An app chat folder has a short name and is
    never refused. The Windows setting is read when this is asked, because a
    conversation worker is a new process and gets the setting of that moment.
    The app itself keeps the setting that it started with. So after long paths
    are turned on, the app's own writes for such a chat still fail until the
    app starts again; this answer is then not given. After they are turned
    off, the answer is given for a folder that the running app could still
    write; a new worker could not. Arithmetic on names; no file below the
    folder is read.
    """
    if not workspace or not windows_refuses_long_paths():
        return None
    try:
        folder = Path(workspace).expanduser().resolve()
        if chat_project_name(folder) is not None or stage_project_name() is not None:
            return None
        size = 1 + len(validate_id(identity))
        over = max(len(str(sessions_dir(folder))) + size + _SESSION_TAIL,
                   len(str(capture_sessions_dir(folder))) + size + _CAPTURE_TAIL) - _FILE_LIMIT
    except (OSError, ValueError, RuntimeError):
        return None  # Not a path or an ID that this can measure. The ordinary checks answer, as before.
    if over <= 0:
        return None
    fits = len(str(folder)) - over
    room = f'{len(str(folder))} characters; at most {fits} fit here' if fits > 3 else f'{len(str(folder))} characters'
    return (f"This chat's folder has a path that is too long for Windows: {folder} ({room}). "
            'Move the folder to a shorter path, or start a chat in another folder.')


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
