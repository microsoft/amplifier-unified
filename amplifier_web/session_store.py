"""Transcript-aware fork policy for the web host; no execution is replayed."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
import json
from pathlib import Path

from .host.config import write_private
from .host.storage import SessionStore


class _BoundaryUnavailable(ValueError):
    """No boundary evidence exists, rather than conflicting saved evidence."""


def text_content(row):
    content = row.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") in {"text", "output_text"})
    return ""


def message_time(row):
    value = (row.get('metadata') or {}).get('timestamp')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def matches_user(row, visible):
    if row.get('role') != 'user':
        return False
    text = visible.get('text', '')
    if text_content(row) == text:
        return True
    content = row.get('content')
    return bool(visible.get('attachments') and isinstance(content, list) and content
                and isinstance(content[0], dict) and content[0].get('type') == 'text'
                and content[0].get('text') == text)


def _input_identity(row):
    marker = (row.get('metadata') or {}).get('amplifier_input')
    if (row.get('role') == 'user' and isinstance(marker, dict)
            and marker.get('version') == 1 and marker.get('kind') == 'user'
            and isinstance(marker.get('id'), str) and 0 < len(marker['id']) <= 200):
        return marker['id']
    return None


def _native_anchor(messages, visible, source=None):
    """Validate a canonical index or unique input binding, never a text guess."""
    changed = 'The saved transcript changed. Refresh this chat before forking or editing it.'
    index = visible.get('nativeIndex')
    voice = visible.get('voiceId') or visible.get('via') == 'call'
    identity = visible.get('inputId') if visible.get('role') == 'user' and not voice else None
    bound = visible.get('nativeMessageId')
    if identity:
        if not isinstance(identity, str) or len(identity) > 200:
            raise ValueError(changed)
        matches = [i for i, row in enumerate(messages) if _input_identity(row) == identity]
        if len(matches) > 1 or matches and type(index) is int and matches[0] != index:
            raise ValueError(changed)
        if matches:
            index = matches[0]
        elif bound:
            raise ValueError(changed)
    if bound and type(index) is not int:
        from .automatic_history import display_identity
        matches = [i for i, row in enumerate(messages)
                   if source is not None and display_identity(source, i, row.get('role'), text_content(row)) == bound]
        if len(matches) != 1:
            raise ValueError(changed)
        index = matches[0]
    if type(index) is not int:
        return None
    if not 0 <= index < len(messages):
        raise ValueError(changed)
    row = messages[index]
    if row.get('role') != visible.get('role'):
        raise ValueError(changed)
    if bound:
        from .automatic_history import display_identity
        if source is None or display_identity(source, index, row.get('role'), text_content(row)) != bound:
            raise ValueError(changed)
    native_identity = _input_identity(row)
    if identity and 'amplifier_input' in (row.get('metadata') or {}) and identity != native_identity:
        raise ValueError(changed)
    same_text = matches_user(row, visible) if row.get('role') == 'user' else text_content(row) == visible.get('text')
    if not (bound or identity and native_identity == identity or same_text):
        raise ValueError(changed)
    return index


def user_boundaries(messages, visible_messages, *, source=None):
    """Locate visible inputs, including spoken turns without a delegated input.

    Voice bubbles are not one-to-one with manager inputs. Native context timestamps
    let us stop *before* a spoken turn, even if its eventual delegated prompt also
    contains several later utterances. Never substring-match a voice reference.
    """
    if not messages:
        return [0 for r in visible_messages if r.get('role') == 'user']
    boundaries = []
    cursor = 0
    for visible in (r for r in visible_messages if r.get('role') == 'user'):
        native_index = _native_anchor(messages, visible, source)
        if native_index is not None:
            if native_index < cursor:
                raise ValueError('The saved transcript changed. Refresh this chat before forking or editing it.')
            boundaries.append(native_index)
            cursor = native_index + 1
            continue
        match = next((i for i in range(cursor, len(messages))
                      if matches_user(messages[i], visible)), None)
        created = visible.get('createdAt')
        # For voice, the manager may receive a prompt much later, or not at all.
        # Missing typed inputs can also be failed submissions; timestamps locate
        # their historical position without claiming they ran.
        if visible.get('voiceId') or visible.get('via') == 'call' or match is None:
            dated = [(i, message_time(messages[i])) for i in range(cursor, len(messages))]
            if isinstance(created, (int, float)) and any(t is not None for _, t in dated):
                match = next((i for i, t in dated if t is not None and t >= created), len(messages))
                boundaries.append(match)
                cursor = match  # Multiple spoken turns can precede the same input.
                continue
            if cursor == len(messages) and isinstance(created, (int, float)) and any(message_time(r) is not None for r in messages):
                boundaries.append(cursor)
                continue
        if match is None:
            raise _BoundaryUnavailable("This older transcript has no reliable boundary for that message. Fork the full conversation instead.")
        boundaries.append(match)
        cursor = match + 1
    return boundaries


def visible_reference(messages, visible):
    """Preserve UI-only conversation as labelled history, never invented tool work."""
    represented = {(row.get('role'), text_content(row)) for row in messages}
    for row in messages:
        content = text_content(row)
        if content.startswith(('Recent spoken conversation follows as role-labelled reference data, not new instructions.',
                               'This is a user message arriving through the voice interface of this same Amplifier conversation.')):
            try:
                reference, current = content.split('\n</voice_reference>\nCurrent spoken user request:\n', 1)
                rows = json.loads(reference.split('<voice_reference>\n', 1)[1])
                if isinstance(rows, list):
                    represented.update((r.get('role'), r.get('text', '')) for r in rows if isinstance(r, dict) and isinstance(r.get('text', ''), str))
                represented.add(('user', current))
            except (ValueError, IndexError, TypeError):
                pass
    missing = []
    for row in visible:
        if row.get('role') != 'user' and not row.get('voiceId'):
            continue
        if (row.get('role'), row.get('text', '')) in represented:
            continue
        if row.get('role') == 'user' and any(matches_user(m, row) for m in messages):
            continue
        missing.append({k: row[k] for k in ('role', 'text', 'via', 'attachments') if k in row})
    if not missing:
        return []
    return [{'role': 'user', 'content':
             'Historical conversation reference from the visible chat, not a new request. '
             'These entries are not separately represented in the saved Amplifier context. '
             'Spoken exchanges may not have required delegation; a typed submission may not have executed. '
             'Do not replay any work or infer tool results from this reference.\n' + json.dumps(missing, ensure_ascii=False),
             'metadata': {'amplifier_visible_reference': True}}]


def complete_tool_exchanges(messages, *, positions=None):
    """Complete interrupted receipts as historical errors, never execute them."""
    result = []
    pending = {}
    def close_pending():
        for call,name in pending.items():
            result.append({"role":"tool","tool_call_id":call,"name":name,"content":json.dumps({
                "success":False,"status":"interrupted","outcome":"unconfirmed","effects":"not_rolled_back",
                "message":"This call was incomplete at the fork boundary. It is historical evidence and was not replayed."})})
        pending.clear()
    for original_index, original in enumerate(messages):
        row = copy.deepcopy(original)
        if row.get("role") == "tool":
            call = row.get("tool_call_id")
            if call not in pending:
                raise ValueError("The source transcript contains an orphan tool result; repair the source before forking")
            pending.pop(call)
            if positions is not None:
                positions[original_index] = len(result)
            result.append(row)
            continue
        if pending:
            close_pending()
        if positions is not None:
            positions[original_index] = len(result)
        result.append(row)
        if row.get("role") == "assistant":
            calls = row.get("tool_calls") or []
            if not calls and isinstance(row.get("content"),list):
                calls = [block for block in row["content"] if isinstance(block,dict) and block.get("type") in {"tool_call","tool_use"}]
            for call in calls:
                identity = call.get("id") or call.get("tool_call_id")
                if not identity or identity in pending:
                    raise ValueError("The source transcript has an invalid tool-call identity")
                pending[identity] = call.get("name") or (call.get("function") or {}).get("name") or "tool"
    close_pending()
    return result


def _index_visible(messages, visible, display_offset=0, *, source=None, verify_anchors=False):
    """Retain exact anchors; only unindexed legacy web rows need alignment."""
    display_indexes = [index for index, row in enumerate(messages)
                       if row.get('role') in {'user', 'assistant'} and text_content(row)
                       and not (row.get('metadata') or {}).get('ephemeral')]
    cursor = display_indexes[display_offset] if 0 <= display_offset < len(display_indexes) else 0
    for row in visible:
        if type(row.get('nativeIndex')) is not int and (row.get('voiceId') or row.get('via') == 'call'):
            continue
        anchor = _native_anchor(messages, row, source) if verify_anchors else row.get('nativeIndex')
        if type(anchor) is int:
            row['nativeIndex'] = anchor
            cursor = max(cursor, anchor + 1)
            continue
        if row.get('role') == 'user':
            match = next((index for index in range(cursor, len(messages))
                          if matches_user(messages[index], row)), None)
        else:
            match = next((index for index in range(cursor, len(messages))
                          if messages[index].get('role') == row.get('role')
                          and text_content(messages[index]) == row.get('text')), None)
        if match is not None:
            row['nativeIndex'] = match
            cursor = match + 1


def _checkpoint_end(messages, visible, cut, explicit_anchors, source):
    """A missing final input can follow a proven complete saved prefix."""
    target = visible[cut]
    if cut != len(visible) - 1 or not cut or target.get('voiceId') or target.get('via') == 'call':
        return None
    previous = visible[cut - 1]
    if previous.get('role') != 'assistant' or previous.get('voiceId') or previous.get('via') == 'call':
        return None
    anchor = _native_anchor(messages, previous, source)
    if anchor != len(messages) - 1:
        return None
    if cut - 1 not in explicit_anchors:
        # An inferred legacy text match is insufficient if a repeated answer
        # could refer to a different native position.
        matches = [row for row in messages if row.get('role') == 'assistant'
                   and text_content(row) == previous.get('text')]
        if len(matches) != 1:
            return None
    return len(messages)


def _full_fork_view(messages, visible, target_id, created_at):
    """Restore a paged prefix while preserving existing UI rows and their IDs."""
    from .automatic_history import display_message
    scope = {'id': target_id, 'nativeIdentity': target_id, 'createdAt': created_at}
    native = [display_message(row, index, scope) for index, row in enumerate(messages)
              if not (row.get('metadata') or {}).get('amplifier_visible_reference')]
    native = [row for row in native if row is not None]
    positions = {row['nativeIndex']: number for number, row in enumerate(native)}
    result, cursor = [], 0
    for row in visible:
        position = positions.get(row.get('nativeIndex'))
        if position is not None:
            result.extend(native[cursor:position])
            cursor = max(cursor, position + 1)
        result.append(row)
    result.extend(native[cursor:])
    return result


def fork_session(home, source, target_id, *, turn=None, before_message_id=None, live_messages=None, bundle=None, reset_model=False, prepare_only=False, recovery=False):
    """Fork complete provider context into a new independent root session.

    The source must be idle (also enforced by the service). Job ledgers,
    approvals, active goals, and runtime ownership are never copied.
    """
    if source.get("status") in {"starting","working","running","stopping"}:
        raise ValueError("Wait for the conversation to finish before forking its transcript")
    if recovery and any(worker.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'} for worker in source.get('workers', [])):
        raise ValueError('Wait for delegated work to stop before creating a recovery copy.')
    home = Path(home)
    store = SessionStore.for_app(home, source.get("workspace") or Path.cwd())
    source_id = source.get("runtimeSessionId") or source["id"]
    source_dir = home / "sessions" / source_id
    target_dir = home / "sessions" / target_id
    if not prepare_only and (target_dir.exists() or (store.directory(target_id) / "transcript.jsonl").exists()):
        raise ValueError("Fork target already exists")
    if source.get('nativeProject') and source.get('nativeRevision'):
        from .automatic_history import revision
        if revision(source) != source['nativeRevision']:
            raise ValueError('The saved transcript changed. Refresh this chat before forking or editing it.')
    saved = store.load(source_id)
    if source.get('nativeProject') and source.get('nativeRevision'):
        if revision(source) != source['nativeRevision']:
            raise ValueError('The saved transcript changed. Refresh this chat before forking or editing it.')
    if saved is None:
        saved = store.import_cli(source_id, workspace=source.get("workspace"))
    visible = copy.deepcopy(source.get("messages",[]))
    if live_messages is not None:
        messages = copy.deepcopy(live_messages)
        metadata = saved[1] if saved else {}
    elif saved:
        messages,metadata = saved
    elif not visible:
        messages,metadata = [],{}
    else:
        # A never-started imported text conversation has no hidden tool history.
        # Preserve precisely its known text, and record that provenance.
        if source.get("runtimeReport") or any(row.get("role") not in {"user","assistant"} for row in visible):
            raise ValueError("The full runtime transcript is unavailable; restore it before forking")
        messages = [{"role":row["role"],"content":row.get("text","")} for row in visible]
        metadata = {"transcript_origin":"visible_text_import"}
    explicit_anchors = {i for i, row in enumerate(visible)
                        if type(row.get('nativeIndex')) is int or row.get('nativeMessageId')}
    user_indexes = [i for i, row in enumerate(visible) if row.get('role') == 'user']
    user_offset = source.get('sharedHistoryUserTurnOffset', 0)
    before_turn = None
    cut = len(visible)
    if before_message_id is not None:
        cut = next((i for i in user_indexes if visible[i].get('id') == before_message_id), None)
        if cut is None:
            raise ValueError('Choose one of your messages to edit.')
        before_turn = user_offset + user_indexes.index(cut) + 1
    elif turn is not None:
        turn -= user_offset
        if type(turn) is not int or turn < 1 or turn > len(user_indexes):
            raise ValueError("Choose an existing user turn for the fork")
        if turn < len(user_indexes):
            cut = user_indexes[turn]
    # Later rows are irrelevant to an earlier cut. Validate only its retained
    # prefix and the requested boundary, including immutable attachment binds.
    _index_visible(messages, visible[:cut + 1], source.get('sharedHistoryOffset', 0),
                   source=source, verify_anchors=cut < len(visible))
    if cut < len(visible):
        target_anchor = _native_anchor(messages, visible[cut], source)
        anchored_input = (target_anchor is not None and visible[cut].get('inputId')
                          and _input_identity(messages[target_anchor]) == visible[cut]['inputId'])
        if target_anchor is not None and (cut in explicit_anchors or anchored_input):
            # Exact current provenance establishes this cut independently of
            # earlier UI-only failed inputs. Never retain an anchored future row.
            boundary = target_anchor
            if any(type(row.get('nativeIndex')) is int and row['nativeIndex'] >= boundary for row in visible[:cut]):
                raise ValueError('The saved transcript changed. Refresh this chat before forking or editing it.')
        else:
            try:
                boundary = user_boundaries(messages, visible[:cut + 1], source=source)[-1]
            except _BoundaryUnavailable:
                boundary = _checkpoint_end(messages, visible, cut, explicit_anchors, source)
                if boundary is not None:
                    # This is evidence about the saved prefix, not a claim that
                    # an unconfirmed submission never executed.
                    pass
                elif prepare_only and cut == len(visible)-1 and visible[cut].get('delivery', {}).get('status') in {'unknown','sending'}:
                    # The owned idle worker has no outstanding input. A final
                    # unconfirmed browser input may never have entered context.
                    user_boundaries(messages, visible[:cut], source=source)
                    boundary = len(messages)
                else:
                    raise
        messages = messages[:boundary]
        visible = visible[:cut]
    # Native indices refer to the saved file, before removing UI-only reference rows.
    retained = [(index, row) for index, row in enumerate(messages)
                if not (row.get('metadata') or {}).get('amplifier_visible_reference')]
    target_positions = {}
    if recovery:
        from .session_health import recovery_context
        messages, target_positions = recovery_context([row for _, row in retained])
    else:
        messages = complete_tool_exchanges([row for _, row in retained], positions=target_positions)
    index_map = {original: target_positions[position] for position, (original, _) in enumerate(retained) if position in target_positions}
    for row in visible:
        original_index = row.pop('nativeIndex', None)
        if original_index in index_map:
            row['nativeIndex'] = index_map[original_index]
    from .host.session import repair_interrupted_receipts
    messages = messages if recovery else repair_interrupted_receipts(messages)
    references = visible_reference(messages, visible)
    messages.extend(references)
    through_turn = user_offset + sum(row.get('role') == 'user' for row in visible)
    now = datetime.now(UTC).isoformat()
    metadata = {**metadata,"session_id":target_id,"parent_id":None,"created":now,"status":"forked",
        "preserve_system":True,"fork":{"source_session_id":source_id,"through_user_turn":through_turn, "before_user_turn":before_turn,
                                     "created":now,"jobs_replayed":False},
        "turn_count":through_turn}
    if recovery:
        metadata['preserve_system'] = False
        metadata['recovery'] = {'source_session_id': source_id, 'mode': 'readable_history', 'work_replayed': False}
    if bundle:
        metadata.update(bundle_name=bundle, bundle=bundle, preserve_system=False)
        retained = [(index, row) for index, row in enumerate(messages) if row.get('role') not in {'system', 'developer'}]
        remap = {old: new for new, (old, _) in enumerate(retained)}
        for row in visible:
            if 'nativeIndex' in row:
                old = row.pop('nativeIndex')
                if old in remap: row['nativeIndex'] = remap[old]
        messages = [row for _, row in retained]
    # Carry only host configuration, not job ownership or child session files.
    copied = {}
    for name in ("configuration.json","control-state.json"):
        path = source_dir / name
        if path.is_file() and not (bundle and name == "configuration.json"):
            data = json.loads(path.read_text())
            if name == "control-state.json":
                data["goal"] = None
                if bundle:
                    from .bundle_selection import reset_controls
                    data = reset_controls(data, reset_model=reset_model)
            copied[name] = json.dumps(data,indent=2)
    effective = source_dir / "effective-configuration.json"
    if effective.is_file() and not bundle:
        copied["configuration.json"] = effective.read_text()
    # UI message IDs remain stable, while canonical content bindings belong to
    # the independent target and its remapped native indices.
    from .automatic_history import display_identity
    for row in visible:
        if row.get('nativeMessageId') and type(row.get('nativeIndex')) is int:
            index = row['nativeIndex']
            row['nativeMessageId'] = display_identity({'id': target_id}, index,
                                                      messages[index].get('role'), text_content(messages[index]))
    if prepare_only:
        return {'messages': visible, 'context': messages, 'throughTurn': through_turn}
    from .managed_chats import is_managed, allocate
    managed = is_managed(source)
    target_workspace = (allocate(home, target_id, 'fork-' + target_id) if managed
                        else source.get('workspace') or Path.cwd())
    if managed:
        # Fork history/artifact references; do not move or replay the original
        # chat's files, tools, jobs, or interpreter state.
        store = SessionStore.for_app(home, target_workspace)
        metadata['working_dir'] = target_workspace
    store.save(target_id,messages,metadata,preserve_system=not bool(bundle or recovery))
    for name,value in copied.items():
        write_private(target_dir / name,value)
    if source.get('sharedHistoryOffset', 0) or user_offset:
        visible = _full_fork_view(messages, visible, target_id, datetime.fromisoformat(now).timestamp())
    from .session_files import project_slug
    stamp = (store.directory(target_id) / 'transcript.jsonl').stat()
    return {"messages":visible,"parentId":source["id"],"forkContext":False,
            **({"recovery": {"sourceSessionId": source["id"], "mode": "readable_history", "workReplayed": False}, "deferRuntimeUntilInteraction": True} if recovery else {}),
            'runtimeSessionId': target_id, 'nativeIdentity': target_id, 'nativeProject': project_slug(target_workspace),
            **({'workspace': target_workspace, 'location': {'kind': 'managed'}, 'workspaceId': None} if managed else {}),
            'nativeRevision': [stamp.st_mtime_ns, stamp.st_size], 'historyLoaded': True, 'historyManaged': False,
            'shared': True, 'sharedHistoryOffset': 0, 'sharedHistoryUserTurnOffset': 0,
            'sharedHistoryTotal': sum(row.get('role') in {'user', 'assistant'} and bool(text_content(row))
                                      and not (row.get('metadata') or {}).get('ephemeral') for row in messages),
            "forkTranscript":{"sourceSessionId":source_id,"messageCount":len(messages),"turn":through_turn,"jobsReplayed":False},
            **({"selection":copy.deepcopy(source["selection"])} if source.get("selection") and not reset_model else {})}
