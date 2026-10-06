"""Installed projection acceptance: hidden voice input must not erase activity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    root = parser.parse_args().root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    workspace.mkdir()
    os.environ["AMPLIFIER_HOME"] = str(root / "native")
    os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"] = str(root / "capture")
    from amplifier_web.automatic_history import read_transcript
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug, sessions_dir
    from amplifier_web.voice_messages import voice_provenance
    import amplifier_web.native_activity as implementation

    assert "/site-packages/" in implementation.__file__
    identity = "voice-activity-acceptance"
    directory = sessions_dir(workspace) / identity
    store = SessionStore(directory.parent, shared=True)
    rows = [
        {"role": "user", "content": "private voice handoff"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "fixture-tool", "type": "function",
             "function": {"name": "inspect", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "fixture-tool", "content": "fixture"},
        {"role": "assistant", "content": "Public answer"},
    ]
    capture = root / "capture" / project_slug(workspace) / "sessions" / identity / "context-intelligence"
    capture.mkdir(parents=True)
    (capture / "events.jsonl").write_text("".join(
        json.dumps({"event": event, "session_id": identity,
                    "timestamp": "2026-01-01T12:00:00Z",
                    "data": {"tool_call_id": "fixture-tool", "tool_name": "inspect"}}) + "\n"
        for event in ("tool:pre", "tool:post")))
    session = {"id": identity, "nativeIdentity": identity,
               "nativeProject": project_slug(workspace), "workspace": str(workspace)}
    store.save(identity, rows, {"working_dir": str(workspace), "bundle": "fixture"})
    control = read_transcript(session, limit=None)
    rows[0]["metadata"] = {"amplifier_input": voice_provenance("voice:call:activity")}
    store.save(identity, rows, {"working_dir": str(workspace), "bundle": "fixture"})
    transcript = directory / "transcript.jsonl"
    before = hashlib.sha256(transcript.read_bytes()).hexdigest()
    voice = read_transcript(session, limit=None)
    after = hashlib.sha256(transcript.read_bytes()).hexdigest()
    checks = {
        "control_has_tool_activity": len(control["activity"]["nodes"]) == 1,
        "private_wrapper_hidden": all(row["text"] != "private voice handoff" for row in voice["messages"]),
        "public_answer_preserved": any(row["text"] == "Public answer" for row in voice["messages"]),
        "tool_activity_preserved": len(voice["activity"]["nodes"]) == 1,
        "canonical_hash_unchanged": before == after,
    }
    result = {"status": "PASS" if all(checks.values()) else "FAIL",
              "checks": checks, "control_tool_nodes": len(control["activity"]["nodes"]),
              "voice_tool_nodes": len(voice["activity"]["nodes"]),
              "implementation": implementation.__file__,
              "transcript_sha256_before": before, "transcript_sha256_after": after,
              "scope": "Installed native-history projection fixture; not live microphone/account acceptance."}
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())