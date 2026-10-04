"""Synthetic canonical histories and read-only native I/O evidence for one fixture.

The audit is a test boundary, not a production observer. It refuses native worker
creation and event-log reads; canonical transcript bytes are counted separately.
"""
import hashlib
import json
import os
from pathlib import Path
import sys


if sys.argv[1] == "seed":
    from amplifier_session_catalog import Catalog
    from amplifier_session_catalog.discovery import Discovery, project_slug

    root = Path(sys.argv[2]).resolve()
    workspace = root / "workspace"
    workspace.mkdir()
    home = root / "native-home"
    sessions = home / "projects" / project_slug(workspace) / "sessions"
    result = []
    catalog = Catalog(root / "catalog.sqlite")
    discovery = Discovery(catalog, [home])
    for number, count in [(1, 10000), (2, 2000)]:
        sid = f"{number:08d}-1111-4111-8111-111111111111"
        selected = sessions / sid
        selected.mkdir(parents=True)
        (selected / "metadata.json").write_text(json.dumps({
            "session_id": sid, "working_dir": str(workspace), "name": f"Cold history {number}",
            "parent_id": None, "turn_count": count, "status": "idle",
        }))
        with (selected / "transcript.jsonl").open("w") as stream:
            for index in range(count):
                stream.write(json.dumps({"role": "user", "content": f"Request {number}:{index}",
                    "metadata": {"amplifier_input": {"id": f"turn-{number}-{index}", "kind": "user", "source": "user"}}}) + "\n")
                stream.write(json.dumps({"role": "assistant", "content": f"Answer {number}:{index}: " + "x" * 512}) + "\n")
        (selected / "events.jsonl").write_bytes(b'{"historical":"preserved"}\n' * 1000)
        record = discovery.hint(sessionDirectory=str(selected))
        result.append({"session": record["uri"], "directory": str(selected), "turns": count,
            "transcriptBytes": (selected / "transcript.jsonl").stat().st_size,
            "hashes": {name: hashlib.sha256((selected / name).read_bytes()).hexdigest()
                for name in ["metadata.json", "transcript.jsonl", "events.jsonl"]}})
        if number == 1:
            for index in range(700):
                child = sessions / f"{sid}_child-{index}"
                child.mkdir()
                (child / "metadata.json").write_text(json.dumps({"working_dir": str(workspace),
                    "parent_id": sid, "name": f"Delegated child {index}", "turn_count": 1}))
                (child / "transcript.jsonl").write_text('{"role":"user","content":"Retained child"}\n')
                discovery.hint(sessionDirectory=str(child))
    print(json.dumps({"workspace": str(workspace), "home": str(home), "sessions": result, "children": 700}))
else:
    mode = sys.argv[1]
    log = Path(os.environ["COLD_IO_LOG"])
    original = Path.open

    def record(row):
        with original(log, "a") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "owner": mode, **row}) + "\n")

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes)):
            name = Path(os.fsdecode(args[0])).name
            if name == "events.jsonl" or mode == "catalog" and name in {"transcript.jsonl", "transcript.jsonl.backup"}:
                record({"event": "forbidden-history-open", "file": name})
                raise AssertionError("Unrequested historical body read")
        if event in {"subprocess.Popen", "os.posix_spawn"}:
            record({"event": "forbidden-worker-start"})
            raise AssertionError("Cold history must not start a worker")

    sys.addaudithook(audit)

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, *args):
            value = self.stream.read(*args)
            record({"event": "transcript-read", "bytes": len(value)})
            return value

    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path.name in {"transcript.jsonl", "transcript.jsonl.backup"}:
            record({"event": "transcript-open"})
            return Reader(stream)
        return stream

    Path.open = opened
    record({"event": "process-start"})
    sys.argv = sys.argv[2:]
    if mode == "catalog":
        from amplifier_session_catalog.__main__ import main
    else:
        from amplifier_acp.server import main
    main()
