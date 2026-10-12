"""Owned-copy history rehearsal. Originals and credentials are never modified."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

if sys.argv[1] == "seed":
    from amplifier_session_catalog import Catalog
    from amplifier_session_catalog.discovery import Discovery, project_slug

    root, donor = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    home = root / "native-home"
    home.mkdir()
    catalog = Catalog(root / "catalog.sqlite")
    discovery = Discovery(catalog, [home])
    rows = []
    for sample in sorted(donor.iterdir()):
        if sample.is_symlink() or not sample.is_dir():
            continue
        metadata = json.loads((sample / "metadata.json").read_text())
        sid = metadata["session_id"]
        assert isinstance(sid, str) and Path(sid).name == sid and sid not in {".", ".."}
        cwd = next(metadata[k] for k in ("working_dir", "cwd", "project_dir", "workspace")
                   if isinstance(metadata.get(k), str) and Path(metadata[k]).is_absolute())
        selected = home / "projects" / project_slug(cwd) / "sessions" / sid
        selected.mkdir(parents=True)
        hashes = {}
        for name in ("metadata.json", "transcript.jsonl", "metadata.json.backup", "transcript.jsonl.backup"):
            source = sample / name
            if not source.is_file():
                continue
            assert not source.is_symlink()
            shutil.copy2(source, selected / name)
            hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        hint = discovery.hint(sessionDirectory=str(selected))
        assert "uri" in hint, hint.get("issues")
        record = catalog.get(hint["uri"])
        grant = Path(cwd)
        while not grant.is_dir():
            grant = grant.parent
        assert grant != Path("/"), "No bounded existing parent for retained workspace"
        rows.append({"sample": sample.name, "session": record["uri"], "kind": record["kind"],
                     "workspace": record["workingDirectory"], "workspaceExists": Path(cwd).is_dir(),
                     "grant": str(grant),
                     "title": record["title"], "directory": str(selected), "hashes": hashes})
    assert rows
    catalog.progress({"phase": "complete", "issues": 0})
    print(json.dumps({"home": str(home), "sessions": rows}))
else:
    mode, root = sys.argv[1], Path(sys.argv[2]).resolve()
    log = root / "native-audit.jsonl"
    original = os.open

    def record(event, **details):
        fd = original(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps({"mode": mode, "event": event, **details}) + "\n").encode())
        finally:
            os.close(fd)

    recording = False

    def audit(event, args):
        global recording
        if recording:
            return
        reason = None
        if event in {"subprocess.Popen", "os.posix_spawn"}:
            reason = "worker-start-refused"
        elif event == "open" and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2] if isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                if not path.is_relative_to(root) and str(path) != "/dev/null":
                    reason = "outside-write-refused"
            if path.name == "events.jsonl":
                reason = "unrequested-events-refused"
        if reason:
            recording = True
            try:
                record(reason, path=str(path) if event == "open" else None)
            finally:
                recording = False
            raise AssertionError(reason)

    sys.addaudithook(audit)
    record("process-start")
    sys.argv = sys.argv[3:]
    if mode == "catalog":
        from amplifier_session_catalog.__main__ import main
    else:
        from amplifier_acp.server import main
    main()
