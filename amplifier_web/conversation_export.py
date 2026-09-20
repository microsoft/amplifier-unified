"""Read-only, complete public conversation snapshots, independent of UI paging."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from amplifier_foundation.session.history import SessionHistoryStore

from .automatic_history import directory, display_message
from .session_files import sessions_dir
from .session_store import _index_visible


def _saved_messages(home, session):
    identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    root = (directory(session) if session.get('nativeProject') else
            sessions_dir(session['workspace']) / identity if session.get('workspace') else None)
    if root is not None and any((root / name).exists() for name in ('transcript.jsonl', 'transcript.jsonl.backup')):
        history = SessionHistoryStore(root, session_id=identity).load(include_events=False)
        if any(item.code == 'changed_during_read' for item in history.diagnostics):
            raise ValueError('The conversation changed during export. Please retry.')
        # A recovered backup is useful for browsing but cannot be described as
        # a complete current export. Do not silently export an older snapshot.
        if any(item.source.startswith('transcript') for item in history.diagnostics):
            raise ValueError('The saved transcript needs recovery before a complete export is available.')
        return history.messages
    legacy = Path(home) / 'sessions' / identity / 'checkpoint.json'
    if legacy.is_file():
        from .host.storage import SessionStore
        return SessionStore(legacy.parent.parent).load(identity)[0]
    if session.get('nativeProject') or session.get('sharedHistoryTotal', 0):
        raise ValueError('The saved transcript is unavailable. Restore it before exporting the complete conversation.')
    return []  # New, imported, and voice-only conversations can be UI-owned.


def _public_reference(row):
    """Decode labelled host history without exporting its provider instructions."""
    text = row.get('text', '')
    if row.get('_visibleReference'):
        try:
            values = json.loads(text.split('\n', 1)[1])
            if not isinstance(values, list):
                raise ValueError('Invalid reference')
            return [item for item in values if isinstance(item, dict)
                    and item.get('role') in {'user', 'assistant'} and isinstance(item.get('text'), str)]
        except (ValueError, IndexError, TypeError):
            raise ValueError('A saved conversation reference could not be read safely.') from None
    if row.get('role') == 'user' and text.startswith('This is a user message arriving through the voice interface of this same Amplifier conversation. '):
        try:
            reference, current = text.split('\n</voice_reference>\nCurrent spoken user request:\n', 1)
            values = json.loads(reference.split('<voice_reference>\n', 1)[1])
            if not isinstance(values, list):
                return None
            rows = [{**item, 'via': 'call'} for item in values if isinstance(item, dict)
                    and item.get('role') in {'user', 'assistant'} and isinstance(item.get('text'), str)]
            if not any((item['role'], item['text']) == ('user', current) for item in rows):
                rows.append({'role': 'user', 'text': current, 'via': 'call'})
            return rows
        except (ValueError, IndexError, TypeError):
            return None  # Ordinary user text is never erased by a partial match.
    return None


def messages(home, session):
    saved = _saved_messages(home, session)
    visible = copy.deepcopy(session.get('messages', []))
    # Validate explicit anchors before mixing two different versions of history.
    for row in visible:
        index = row.get('nativeIndex')
        if type(index) is int:
            if not 0 <= index < len(saved):
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
            canonical = display_message(saved[index], index, session)
            if canonical is None:
                if (saved[index].get('metadata') or {}).get('ephemeral'):
                    continue  # Omit old UI copies of now-hidden ephemeral rows.
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
            from .session_store import matches_user
            if (canonical['role'], canonical['text']) != (row.get('role'), row.get('text')) and not matches_user(saved[index], row):
                raise ValueError('The saved conversation was rewritten. Refresh it before exporting.')
    visible = [row for row in visible if type(row.get('nativeIndex')) is not int
               or (0 <= row['nativeIndex'] < len(saved) and display_message(saved[row['nativeIndex']], row['nativeIndex'], session) is not None)]
    # Main-session replies to a voice delegation are canonical chat messages;
    # only recorded voice items are separate, UI-owned spoken exchanges.
    calls = [row for row in visible if row.get('via') == 'call' and not row.get('voiceId')]
    for row in calls:
        row['via'] = 'chat'
    _index_visible(saved, visible, session.get('sharedHistoryOffset', 0))
    for row in calls:
        row['via'] = 'call'
    native = []
    for index, row in enumerate(saved):
        displayed = display_message(row, index, session)
        if displayed is not None:
            if (row.get('metadata') or {}).get('amplifier_visible_reference'):
                displayed['_visibleReference'] = True
            native.append(displayed)
    positions = {row['nativeIndex']: index for index, row in enumerate(native)}
    combined, cursor = [], 0
    for row in visible:
        position = positions.get(row.get('nativeIndex'))
        if position is not None:
            combined.extend(native[cursor:position])
            cursor = max(cursor, position + 1)
            if native[position].get('_visibleReference'):
                row['_visibleReference'] = True
        combined.append(row)
    combined.extend(native[cursor:])
    # Legacy reference records contain ordered role/text/via values, but no
    # stable message IDs or timestamps. Match *occurrences*, never a text set:
    # two separate "yes" turns remain two turns. Reference-only recovery stays
    # at its saved native anchor and is labelled as having uncertain placement.
    public_ui = [row for row in visible if not row.get('_visibleReference') and _public_reference(row) is None]
    reference_claims = set()
    voice_window = []
    voice_ui_cursor = 0
    result = []
    for row in combined:
        if row.get('role') not in {'user', 'assistant'}:
            continue
        reference = _public_reference(row)
        if reference is not None:
            if not row.get('_visibleReference'):
                # Consecutive voice delegations carry overlapping recent-speech
                # windows. Remove only a contiguous suffix/prefix overlap;
                # never collapse repeated occurrences inside a window.
                keys = [(item['role'], item['text']) for item in reference]
                overlap = next((size for size in range(min(len(voice_window), len(keys)), 0, -1)
                                if voice_window[-size:] == keys[:size]), 0)
                voice_window.extend(keys[overlap:])
                reference = reference[overlap:]
            ui_cursor = 0 if row.get('_visibleReference') else voice_ui_cursor
            for item in reference:
                match = next((index for index in range(ui_cursor, len(public_ui))
                              if (not row.get('_visibleReference') or index not in reference_claims)
                              and (public_ui[index].get('role'), public_ui[index].get('text')) == (item['role'], item['text'])
                              and (not item.get('via') or public_ui[index].get('via') == item['via'])), None)
                if match is not None:
                    ui_cursor = match + 1
                    if row.get('_visibleReference'):
                        reference_claims.add(match)
                else:
                    result.append({**item, '_recoveredReference': True})
            if not row.get('_visibleReference'):
                voice_ui_cursor = ui_cursor
        else:
            result.append(row)
    return result


def _label(value):
    return re.sub(r'[\r\n]+', ' ', str(value)).replace('`', '\\`')


def markdown(home, session, artifacts):
    rows = messages(home, session)
    blocks = ['# ' + _label(session.get('title') or 'Conversation'),
              'Conversation: `' + _label(session['id']) + '`',
              'Snapshot of user-visible conversation text. Tool payloads and hidden instructions are omitted.']
    if session.get('status') in {'working', 'starting', 'running', 'stopping'}:
        blocks.append('Work was in progress when this snapshot was captured; the final response may follow later.')
    if any(row.get('_recoveredReference') for row in rows):
        blocks.append('Recovered reference entries retain their saved reference position. Their original conversation positions are unavailable.')
    for row in rows:
        role = 'User' if row['role'] == 'user' else 'Assistant'
        if row.get('observation'):
            role = 'Service observation'
        if row.get('via') == 'call' or row.get('voiceId'):
            role += ' (voice)'
        if row.get('_recoveredReference'):
            role += ' — recovered reference'
        blocks.append('## ' + role)
        # Deliberately no strip/normalization: Markdown fences, indentation and
        # trailing spaces in visible source are part of the exported message.
        blocks.append(row.get('text', ''))
        for item in row.get('attachments', []):
            if isinstance(item, dict):
                blocks.append('Attachment: ' + _label(item.get('name', 'File')) + ' (ID: `' + _label(item.get('id', 'unavailable')) + '`)')
    owned = [row for row in artifacts if row.get('sessionId') == session['id']]
    if owned:
        blocks.append('## Artifacts\n\nReferences identify saved artifacts in this host; their contents are not embedded.')
        for row in owned:
            blocks.append('- ' + _label(row.get('title') or row.get('kind') or 'Artifact')
                          + ' — ID: `' + _label(row['id']) + '`'
                          + ('; message: `' + _label(row['messageId']) + '`' if row.get('messageId') else ''))
    if not rows:
        blocks.append('No messages yet.')
    return '\n\n'.join(blocks) + '\n'
