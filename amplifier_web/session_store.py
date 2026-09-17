"""Transcript-aware fork policy for the web host; no execution is replayed."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
import json
from pathlib import Path

from .host.config import write_private
from .host.storage import SessionStore


def text_content(row):
    content = row.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def user_boundaries(messages, visible_messages):
    """Match visible user turns to transcript rows, excluding service observations."""
    expected = [row.get("text", "") for row in visible_messages if row.get("role") == "user"]
    boundaries = []
    cursor = 0
    for text in expected:
        match = next((index for index in range(cursor,len(messages))
                      if messages[index].get("role") == "user" and text_content(messages[index]) == text),None)
        if match is None:
            raise ValueError("The saved transcript does not contain every visible user turn yet. Wait for the session to finish before forking it.")
        boundaries.append(match)
        cursor = match + 1
    return boundaries


def complete_tool_exchanges(messages):
    """Complete interrupted receipts as historical errors, never execute them."""
    result = []
    pending = {}
    def close_pending():
        for call,name in pending.items():
            result.append({"role":"tool","tool_call_id":call,"name":name,"content":json.dumps({
                "success":False,"status":"interrupted","outcome":"unconfirmed","effects":"not_rolled_back",
                "message":"This call was incomplete at the fork boundary. It is historical evidence and was not replayed."})})
        pending.clear()
    for original in messages:
        row = copy.deepcopy(original)
        if row.get("role") == "tool":
            call = row.get("tool_call_id")
            if call not in pending:
                raise ValueError("The source transcript contains an orphan tool result; repair the source before forking")
            pending.pop(call)
            result.append(row)
            continue
        if pending:
            close_pending()
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


def fork_session(home, source, target_id, *, turn=None, live_messages=None):
    """Fork complete provider context into a new independent root session.

    The source must be idle (also enforced by the service). Job ledgers,
    approvals, active goals, and runtime ownership are never copied.
    """
    if source.get("status") in {"starting","working","stopping"}:
        raise ValueError("Wait for the conversation to finish before forking its transcript")
    home = Path(home)
    store = SessionStore(home / "sessions")
    source_id = source.get("runtimeSessionId") or source["id"]
    source_dir, target_dir = store.directory(source_id), store.directory(target_id)
    if target_dir.exists():
        raise ValueError("Fork target already exists")
    saved = store.load(source_id)
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
    boundaries = user_boundaries(messages,visible)
    if turn is not None:
        if type(turn) is not int or turn < 1 or turn > len(boundaries):
            raise ValueError("Choose an existing user turn for the fork")
        end = boundaries[turn] if turn < len(boundaries) else len(messages)
        messages = messages[:end]
        seen = 0
        for index,row in enumerate(visible):
            if row.get("role") == "user":
                seen += 1
                if seen > turn:
                    visible = visible[:index]
                    break
    messages = complete_tool_exchanges(messages)
    from .host.session import repair_interrupted_receipts
    messages = repair_interrupted_receipts(messages)
    now = datetime.now(UTC).isoformat()
    metadata = {**metadata,"session_id":target_id,"parent_id":None,"created":now,"status":"forked",
        "preserve_system":True,"fork":{"source_session_id":source_id,"through_user_turn":turn or len(boundaries),
                                     "created":now,"jobs_replayed":False},
        "turn_count":turn or len(boundaries)}
    # Carry only host configuration, not job ownership or child session files.
    copied = {}
    for name in ("configuration.json","control-state.json"):
        path = source_dir / name
        if path.is_file():
            data = json.loads(path.read_text())
            if name == "control-state.json":
                data["goal"] = None
            copied[name] = json.dumps(data,indent=2)
    effective = source_dir / "effective-configuration.json"
    if effective.is_file():
        copied["configuration.json"] = effective.read_text()
    store.save(target_id,messages,metadata,preserve_system=True)
    for name,value in copied.items():
        write_private(target_dir / name,value)
    return {"messages":visible,"parentId":source["id"],"forkContext":False,
            "forkTranscript":{"sourceSessionId":source_id,"messageCount":len(messages),"turn":turn or len(boundaries),"jobsReplayed":False},
            **({"selection":copy.deepcopy(source["selection"])} if source.get("selection") else {})}
