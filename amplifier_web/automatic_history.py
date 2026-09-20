"""Automatic navigation over native sessions; transcripts stay in their files."""
from __future__ import annotations

import asyncio
from collections import deque
import copy
import hashlib
import json
from pathlib import Path
import time
import uuid

from .session_files import amplifier_home, project_slug
from amplifier_foundation.session.history import SessionHistoryStore
from .shared_state_probe import text_content

BUSY = {'starting', 'working', 'running', 'stopping', 'ready'}
INDEX_FIELDS = ('draft', 'id', 'title', 'titleSource', 'nativeNameSource', 'description', 'bundle', 'workspace',
                'workspaceId', 'workspaceAvailable', 'createdAt', 'updatedAt', 'recentActivityAt',
                'runtimeSessionId', 'nativeIdentity', 'nativeProject', 'parentId', 'nativeParentId',
                'nativeRevision', 'nativeBoundary', 'nativeBoundaryId', 'turnCount', 'shared',
                'historyManaged', 'historyReadOnlyReason', 'draftAttachments', 'sessionKind')


def identity(project, session):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{project}/{session}').hex


def directory(session):
    project = session['nativeProject']
    if not isinstance(project, str) or project in {'', '.', '..'} or Path(project).name != project:
        raise ValueError('Invalid native project identity.')
    root = amplifier_home() / 'projects'
    session_id = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    if not isinstance(session_id, str) or session_id in {'', '.', '..'} or '/' in session_id or '\\' in session_id or '\x00' in session_id:
        raise ValueError('Invalid native session identity.')
    path = root / project / 'sessions' / session_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Session storage must remain inside the shared projects folder.')
    return path


def revision(session):
    for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
        try:
            info = (directory(session) / name).stat()
            return [info.st_mtime_ns, info.st_size]
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def display_identity(session, index, role, text):
    key = json.dumps([session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'], index, role, text], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def display_message(row, index, session, *, include_internal=False):
    if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}:
        return None
    if not include_internal and (row.get('metadata') or {}).get('ephemeral'):
        return None
    text = text_content(row)
    if not text:
        return None
    from .session_store import message_time
    metadata=row.get('metadata') or {}
    provenance=metadata.get('amplifier_input',{})
    recovery=metadata.get('live_recovery_job')
    if 'amplifier_input' not in metadata and isinstance(recovery,str) and 0<len(recovery)<=128:
        # Earlier loop-live versions recorded recovery ownership separately.
        # Read that host metadata; matching text alone is never provenance.
        provenance={'version':1,'kind':'service','id':recovery,'source':'local-job-recovery'}
    observation={}
    if isinstance(provenance,dict) and provenance.get('version')==1 and provenance.get('kind')=='service' and all(isinstance(provenance.get(key),str) and 0<len(provenance[key])<=128 for key in ('id','source')):
        observation={'observation':{key:provenance[key] for key in ('id','source','call_id') if key in provenance}}
    return {'id': display_identity(session, index, row['role'], text), 'role': row['role'],
            'text': text, 'via': 'chat', 'source': 'native', 'nativeIndex': index,
            'createdAt': message_time(row) or session.get('createdAt', 0), **observation}


def read_transcript(session, *, before=None, limit=100):
    """Read a display page, or a full view for ordered web-history merging."""
    from .native_activity import activity_page
    root = directory(session)
    native_id = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    # Relocation is CI policy, independent of transcript storage. It also works
    # for read-only historical child IDs that cannot start a new manager.
    import os
    raw_root = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    event_root = Path(raw_root).expanduser() if raw_root and '${' not in raw_root else None
    events = (event_root / session['nativeProject'] / 'sessions' / native_id /
              'context-intelligence' / 'events.jsonl') if event_root is not None and event_root.is_absolute() else None
    reader = SessionHistoryStore(root, events_path=events, session_id=native_id)
    start = revision(session)
    history = reader.load(include_events=False)
    rows = deque(maxlen=limit)
    total = users = 0
    hidden = {}
    for index, value in enumerate(history.messages):
        row = display_message(value, index, session)
        if row is None:
            internal = display_message(value, index, session, include_internal=True)
            if internal is not None:
                hidden[index] = internal['id']
            continue
        if before is None or total < before:
            rows.append((total, users, row))
        total += 1
        users += row['role'] == 'user'
    if revision(session) != start or any(d.code == 'changed_during_read' for d in history.diagnostics):
        raise ValueError('The CLI is saving this chat. Its history will refresh shortly.')
    visible = [row[2] for row in rows]
    activity = activity_page(reader, history.messages, visible)
    activity['diagnostics'] = [dict(code=d.code, source=d.source, line=d.line, severity=d.severity)
                               for d in history.diagnostics] + activity['diagnostics']
    return {'messages': visible, 'offset': rows[0][0] if rows else 0,
            'userOffset': rows[0][1] if rows else 0, 'total': total, 'revision': start,
            'activity': activity, 'hiddenMessages': hidden}


def remove_internal_copies(session, hidden):
    """Remove old UI copies only when native index, role and full text agree.

    The reader classified these rows from canonical metadata. Tags or matching
    text alone are never evidence that a user's message should be hidden.
    Canonical messages and their resume context are not changed.
    """
    session['messages'] = [message for message in session['messages']
        if type(message.get('nativeIndex')) is not int
        or hidden.get(message['nativeIndex']) != display_identity(
            session, message['nativeIndex'], message.get('role'), message.get('text', ''))]
    boundary = session.get('nativeBoundary')
    if type(boundary) is int and boundary in hidden and hidden[boundary] == session.get('nativeBoundaryId'):
        # A prior reader could anchor its append cursor on a trailing reminder.
        # Re-establish it from the last retained, verified native message.
        session.pop('nativeBoundary', None)
        session.pop('nativeBoundaryId', None)


def merge_web_history(session, incoming):
    """Append native messages after an ordered boundary, preserving web IDs.

    Native indexes disambiguate repeated prompts. Initial alignment is ordered,
    and every subsequent read verifies the boundary before appending anything.
    The full native text view is provided so a long CLI continuation cannot
    fall outside a fixed-size tail window and disappear from the web view.
    Legacy UI rows without indexes use ordered text alignment; distinguishing
    an identical failed prompt from a saved prompt would require an input ledger.
    """
    current = session['messages']
    indexed = {message['nativeIndex']: (number, message) for number, message in enumerate(incoming)}
    boundary = session.get('nativeBoundary')
    boundary_id = session.get('nativeBoundaryId')
    cursor = start = 0
    if boundary is None:
        # A just-promoted native chat already has stable native indexes.
        anchored = [(number, message) for number, message in enumerate(current)
                    if isinstance(message.get('nativeIndex'), int)]
        if anchored:
            number, message = max(anchored, key=lambda item: item[1]['nativeIndex'])
            match = indexed.get(message['nativeIndex'])
            if not match or (match[1]['role'], match[1]['text']) != (message['role'], message.get('text')):
                raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
            cursor, start = match[0] + 1, number + 1
    elif boundary >= 0:
        match = indexed.get(boundary)
        if not match or match[1]['id'] != boundary_id:
            raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
        cursor = match[0] + 1
        start = next((number + 1 for number in range(len(current) - 1, -1, -1)
                      if current[number].get('nativeIndex') == boundary), len(current))
    merged = current[:start]
    for message in current[start:]:
        match = next((number for number in range(cursor, len(incoming))
                      if (incoming[number]['role'], incoming[number]['text']) ==
                         (message.get('role'), message.get('text'))), None)
        if match is not None:
            merged.extend(incoming[cursor:match])
            message['nativeIndex'] = incoming[match]['nativeIndex']
            cursor = match + 1
        merged.append(message)
    merged.extend(incoming[cursor:])
    for message in merged:
        match=indexed.get(message.get('nativeIndex'))
        if match and (match[1]['role'],match[1]['text'])==(message.get('role'),message.get('text')) and match[1].get('observation'):
            # Upgrade already-saved display copies without changing identities,
            # canonical history, or similarly worded user messages.
            message['observation']=copy.deepcopy(match[1]['observation'])
    session['messages'] = merged
    session['nativeBoundary'] = incoming[-1]['nativeIndex'] if incoming else -1
    session['nativeBoundaryId'] = incoming[-1]['id'] if incoming else None


class AutomaticHistory:
    def __init__(self, service):
        from .native_history import NativeHistory
        self.service = service
        self.index = NativeHistory()
        self.lock = asyncio.Lock()
        self.loads = {}
        self.task = None
        self.last_scan = None
        service.state.setdefault('sharedHistory', {}).update(loading=True, error=None)

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def loop(self):
        while not self.service.closed:
            await self.refresh()
            await asyncio.sleep(15)

    def hide_session(self, session):
        project = session.get('nativeProject') or project_slug(session['workspace'])
        key = identity(project, session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'])
        hidden = self.service.state.setdefault('hiddenNativeSessions', [])
        if key not in hidden:
            hidden.append(key)

    def hide_workspace(self, workspace):
        hidden = self.service.state.setdefault('hiddenNativeWorkspaces', [])
        if workspace['id'] not in hidden:
            hidden.append(workspace['id'])

    async def refresh(self):
        async with self.lock:
            try:
                known = copy.deepcopy(self.service.state['workspaces'])
                from .workspace_canvas import refresh_workspace_availability
                await asyncio.to_thread(refresh_workspace_availability, known)
                snapshot = await asyncio.to_thread(self.index.scan, known_workspaces=known)
                if self.service.closed:
                    return
                async with self.service.lock:
                    state = self.service.state
                    changed = bool(state.get('sharedHistory', {}).get('loading') or state.get('sharedHistory', {}).get('error'))
                    hidden_workspaces = set(state.get('hiddenNativeWorkspaces', []))
                    workspaces = {row['id']: row for row in state['workspaces']}
                    # Most refreshes contain no unresolved folders. Index the
                    # exceptions once instead of rescanning every registration
                    # for every discovered workspace while holding the app lock.
                    unresolved_by_project = {}
                    for item in state['workspaces']:
                        if not item.get('path'):
                            unresolved_by_project.setdefault(item.get('nativeProject'), {})[item['id']] = item
                    for row in known:
                        previous = workspaces.get(row['id'])
                        if previous and previous.get('path') == row.get('path') and previous.get('available') != row['available']:
                            previous['available'] = row['available']
                            changed = True
                    for row in snapshot['workspaces']:
                        unresolved_id = uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-project:{row['nativeProject']}").hex
                        if row.get('path') and unresolved_id in hidden_workspaces and row['id'] not in hidden_workspaces:
                            hidden_workspaces.add(row['id'])
                            state.setdefault('hiddenNativeWorkspaces', []).append(row['id'])
                            changed = True
                        if row['id'] in hidden_workspaces:
                            continue
                        previous = workspaces.get(row['id'])
                        previous_project = previous.get('nativeProject') if previous else None
                        unresolved = [item for identity, item in unresolved_by_project.get(row['nativeProject'], {}).items()
                                      if identity != row['id']]
                        if previous is None:
                            state['workspaces'].append(dict(row)); workspaces[row['id']] = state['workspaces'][-1]
                            previous = workspaces[row['id']]
                            changed = True
                        else:
                            for key in ('nativeProject', 'available', 'sessionCount', 'workerSessionCount'):
                                if key in row and previous.get(key) != row[key]:
                                    previous[key] = row[key]; changed = True
                        if not previous.get('path'):
                            # Later rows in this same scan must see additions
                            # and identity changes, just as a fresh list scan did.
                            project = previous.get('nativeProject')
                            if previous_project != project:
                                unresolved_by_project.get(previous_project, {}).pop(previous['id'], None)
                            unresolved_by_project.setdefault(project, {})[previous['id']] = previous
                        for old in unresolved:
                            # Migrate the generated placeholder, preserving any
                            # user customization and every reference to it.
                            if old.get('name') != old.get('nativeProject'):
                                if previous.get('name') == row.get('name'):
                                    previous['name'] = old['name']
                                elif old.get('name') != previous.get('name'):
                                    aliases = previous.setdefault('aliases', [])
                                    if old['name'] not in aliases:
                                        aliases.append(old['name'])
                            for key, value in old.items():
                                if key not in {'id', 'name', 'nativeProject', 'path', 'available', 'sessionCount', 'workerSessionCount'}:
                                    previous.setdefault(key, copy.deepcopy(value))
                            if state.get('selectedWorkspaceId') == old['id']:
                                state['selectedWorkspaceId'] = row['id']
                                if row.get('path'):
                                    state['settings']['workspace'] = row['path']
                            for scoped in [*state['sessions'], *state.get('canvasArtifacts', []), state.get('canvas', {})]:
                                if scoped.get('workspaceId') == old['id']:
                                    scoped['workspaceId'] = row['id']
                            state['workspaces'].remove(old)
                            workspaces.pop(old['id'], None)
                            unresolved_by_project.get(old.get('nativeProject'), {}).pop(old['id'], None)
                            changed = True
                    existing = {(s.get('nativeProject') or (project_slug(s['workspace']) if s.get('workspace') else None),
                                 s.get('nativeIdentity') or s.get('runtimeSessionId') or s['id']): s for s in state['sessions']}
                    hidden = set(state.get('hiddenNativeSessions', []))
                    for row in snapshot['sessions']:
                        key = (row['nativeProject'], row['nativeIdentity'])
                        if identity(*key) in hidden or row['workspaceId'] in hidden_workspaces:
                            continue
                        previous = existing.get(key)
                        if previous is None:
                            previous = {'id': row['id'], 'title': row.get('name') or row.get('title') or 'Conversation ' + row['nativeIdentity'][:8],
                                        'titleSource': 'native', 'nativeNameSource': row.get('nameSource'), 'bundle': row.get('bundle') or state['settings']['bundle'],
                                        'workspace': row.get('workspace'), 'workspaceId': row['workspaceId'],
                                        'workspaceAvailable': workspaces.get(row['workspaceId'], {}).get('available', False),
                                        'createdAt': row.get('createdAt', 0), 'updatedAt': row.get('updatedAt', 0), 'recentActivityAt': row.get('recentActivityAt', 0),
                                        'status': 'idle', 'messages': [], 'workers': [], 'approvals': [],
                                        'runtimeSessionId': row['nativeIdentity'], 'nativeIdentity': row['nativeIdentity'],
                                        'nativeProject': row['nativeProject'], 'nativeRevision': row.get('transcriptRevision'),
                                        'parentId': row.get('parentId'), 'nativeParentId': row.get('parentId'), 'turnCount': row.get('turnCount'),
                                        'sessionKind': row['sessionKind'],
                                        'description': row.get('description', ''), 'shared': True,
                                        'historyReadOnlyReason': row.get('readOnlyReason'),
                                        'historyManaged': True, 'historyLoaded': False}
                            state['sessions'].append(previous); existing[key] = previous; changed = True
                        else:
                            from .chat_navigation import recent_activity
                            recent = max(recent_activity(previous), row.get('recentActivityAt', 0))
                            if previous.get('recentActivityAt') != recent:
                                previous['recentActivityAt'] = recent; changed = True
                            for key_name, value in {'nativeProject': row['nativeProject'], 'nativeIdentity': row['nativeIdentity'],
                                                    'nativeNameSource': row.get('nameSource'), 'workspaceId': row['workspaceId'],
                                                    'sessionKind': row['sessionKind'],
                                                    'workspaceAvailable': workspaces.get(row['workspaceId'], {}).get('available', False)}.items():
                                if previous.get(key_name) != value:
                                    previous[key_name] = value; changed = True
                            if previous.get('historyManaged') and previous.get('titleSource') != 'manual':
                                title = row.get('name') or row.get('title') or previous['title']
                                if title != previous['title']:
                                    previous['title'] = title; changed = True
                            if previous.get('historyManaged') and previous.get('historyReadOnlyReason') != row.get('readOnlyReason'):
                                previous['historyReadOnlyReason'] = row.get('readOnlyReason'); changed = True
                            if previous.get('historyManaged'):
                                metadata = {'workspace': row.get('workspace'), 'description': row.get('description', ''),
                                            'createdAt': row.get('createdAt', 0), 'updatedAt': row.get('updatedAt', 0),
                                            'turnCount': row.get('turnCount'), 'nativeParentId': row.get('parentId'),
                                            'nativeNameSource': row.get('nameSource')}
                                if not row.get('parentId'):
                                    metadata['parentId'] = None
                                if row.get('bundle'):
                                    metadata['bundle'] = row['bundle']
                                for key_name, value in metadata.items():
                                    if previous.get(key_name) != value:
                                        previous[key_name] = value; changed = True
                            if not previous.get('historyLoaded') and previous.get('historyManaged'):
                                previous['nativeRevision'] = row.get('transcriptRevision')
                            elif 'nativeRevision' not in previous:
                                previous['nativeRevision'] = row.get('transcriptRevision')
                        previous['_catalogRecentAt'] = row.get('recentActivityAt', 0)
                        previous['_catalogId'] = row['id']
                    # Parent identities belong to their native project. UI IDs
                    # are aliases and can differ even when a web root predated
                    # automatic discovery or another project reused the ID.
                    for row in snapshot['sessions']:
                        session = existing.get((row['nativeProject'], row['nativeIdentity']))
                        if session is None or not row.get('parentId'):
                            continue
                        parent = existing.get((row['nativeProject'], row['parentId']))
                        parent_id = parent['id'] if parent else row['parentId']
                        for key_name, value in {'parentId': parent_id, 'nativeParentId': row['parentId']}.items():
                            if session.get(key_name) != value:
                                session[key_name] = value; changed = True
                    state['sharedHistory'].update(loading=False, error=None,
                        projectCount=len(snapshot['workspaces']),
                        sessionCount=sum(row['sessionKind'] == 'root' for row in snapshot['sessions']),
                        workerSessionCount=sum(row['sessionKind'] == 'worker' for row in snapshot['sessions']))
                    self.last_scan = snapshot
                    if changed:
                        self.service._publish()
                selected = next((s for s in self.service.state['sessions'] if s['id'] == self.service.state.get('selectedSessionId')), None)
                if selected and selected.get('nativeProject') and selected.get('status') not in BUSY and not selected.get('configurationBusy'):
                    current = await asyncio.to_thread(revision, selected)
                    if not selected.get('historyLoaded', True) or current != selected.get('nativeRevision'):
                        await self.load(selected['id'])
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self.service.closed:
                    async with self.service.lock:
                        self.service.state['sharedHistory'].update(loading=False, error='Could not refresh shared chat history. Existing chats are kept; try Refresh.')
                        self.service._publish()

    async def load(self, session_id, *, before=None, limit=100):
        async with self.loads.setdefault(session_id, asyncio.Lock()):
            async with self.service.lock:
                session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                if not session or not session.get('nativeProject') or session.get('status') in BUSY or session.get('configurationBusy'):
                    return
                session.update(historyLoading=True, historyError=None)
                source = copy.deepcopy(session)
                self.service._publish()
            try:
                window = (max(limit, len(source.get('messages', []))) if source.get('historyManaged') else None) if before is None else limit
                result = await asyncio.to_thread(read_transcript, source, before=before, limit=window)
                async with self.service.lock:
                    session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                    if session is None:
                        return
                    if session.get('status') not in BUSY and not session.get('configurationBusy'):
                        remove_internal_copies(session, result['hiddenMessages'])
                        if before is not None:
                            if source.get('nativeRevision') != result['revision']:
                                raise ValueError('The saved chat changed. Refresh it before loading earlier messages.')
                            known = {m['id'] for m in session['messages']}
                            known_indexes = {m['nativeIndex'] for m in session['messages'] if 'nativeIndex' in m}
                            session['messages'] = [m for m in result['messages']
                                                   if m['id'] not in known and m['nativeIndex'] not in known_indexes] + session['messages']
                        elif session.get('historyManaged'):
                            # Keep the loaded window when another host appends.
                            session['messages'] = result['messages']
                        else:
                            # Preserve web-owned message IDs, execution anchors,
                            # voice-only bubbles, attachments and draft state.
                            merge_web_history(session, result['messages'])
                        from .native_activity import apply_activity
                        apply_activity(session, result['activity'], append=before is not None)
                        offset = source.get('sharedHistoryOffset', 0)
                        user_offset = source.get('sharedHistoryUserTurnOffset', 0)
                        if session.get('historyManaged') or (before is not None and result['offset'] <= offset):
                            offset, user_offset = result['offset'], result['userOffset']
                        session.update(historyLoaded=True, nativeRevision=result['revision'],
                            sharedHistoryOffset=offset, sharedHistoryUserTurnOffset=user_offset,
                            sharedHistoryTotal=result['total'])
                    session['historyLoading'] = False
                    self.service._publish()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.service.lock:
                    session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                    if session:
                        session.update(historyLoading=False, historyError='Could not read the saved chat. Its original files are unchanged. Try Refresh.')
                        self.service._publish()

    async def ensure_loaded(self, session_id):
        session = self.service._session(session_id)
        if session.get('nativeProject'):
            if session.get('historyReadOnlyReason'):
                raise ValueError(session['historyReadOnlyReason'])
            if session.get('workspaceAvailable') is False or not session.get('workspace'):
                raise ValueError('This project folder is unavailable. Its saved chats can be read, but the folder must be restored before continuing work.')
            current_revision = await asyncio.to_thread(revision, copy.deepcopy(session))
            if not session.get('historyLoaded', True) or session.get('historyLoading') or current_revision != session.get('nativeRevision'):
                await self.load(session_id)
                session = self.service._session(session_id)
            if session.get('historyError'):
                raise ValueError(session['historyError'])
