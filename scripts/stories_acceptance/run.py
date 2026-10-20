"""Run the optional Stories owner against an already isolated, running Unified.

No dependency installation, production service restart, provider calls or skips.
The caller owns the DTU, prepared Work runtime, browser and source checkouts.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
import socket
import sqlite3
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from datetime import datetime, timezone

WORK_REVISION = "50deb9ebb4b9407401c45e624940effc93cfb107"
AGENT_REVISION = "44a83adeb49fc1cfb55eb0c1bf38aa125ff0d705"


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owned-dtu", required=True, help="Recorded owner/container name")
    p.add_argument("--url", required=True)
    p.add_argument(
        "--app-db",
        type=Path,
        required=True,
        help="Owned app database; read-only replay witness",
    )
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument(
        "--state",
        type=Path,
        required=True,
        help="New, private evidence/state directory",
    )
    p.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="New private bundle/provider directory",
    )
    p.add_argument("--stories-source", type=Path, required=True)
    p.add_argument("--stories-revision", required=True)
    p.add_argument("--node", type=Path, required=True)
    p.add_argument("--playwright", type=Path, required=True)
    args = p.parse_args()
    if socket.gethostname() != args.owned_dtu:
        raise RuntimeError("Run inside the named owned DTU; hostname does not match")
    if urlparse(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Run inside the owned DTU against its loopback listener")
    args.state = args.state.resolve()
    args.fixture = args.fixture.resolve()
    # Fail before app effects if any external prerequisites are missing or drifted.
    source = args.stories_source.resolve(strict=True)
    revision = command("git", "-C", str(source), "rev-parse", "HEAD")
    if revision != args.stories_revision:
        raise RuntimeError(f"Stories revision drift: {revision}")
    if command(
        "git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"
    ):
        raise RuntimeError("Stories has tracked source changes")
    cli, mcp, python = [
        source / ".venv/bin" / n for n in ("stories", "stories-mcp", "python")
    ]
    for path in (cli, mcp, python, args.node, args.playwright, args.token_file):
        if not path.is_file():
            raise RuntimeError(f"Missing prerequisite: {path}")
    dependencies = json.loads(
        command(
            str(python),
            "-c",
            """
import importlib.metadata as m, json
d=m.distribution('amplifier-agent')
print(json.dumps({'agentVersion':d.version,'agentOrigin':json.loads(d.read_text('direct_url.json')),
'mcpVersion':m.version('mcp'),'storiesVersion':m.version('amplifier-smart-tool-stories'),
'storiesOrigin':json.loads(m.distribution('amplifier-smart-tool-stories').read_text('direct_url.json'))}))
""",
        )
    )
    if (
        dependencies["agentOrigin"].get("vcs_info", {}).get("commit_id")
        != AGENT_REVISION
    ):
        raise RuntimeError(
            "Unqualified Agent dependency; qualify a new source explicitly"
        )
    origin = dependencies["storiesOrigin"]
    if "vcs_info" in origin:
        if origin["vcs_info"].get("commit_id") != revision:
            raise RuntimeError(
                "Installed Stories source does not match the requested revision"
            )
    elif not (
        origin.get("dir_info", {}).get("editable")
        and urlparse(origin["url"]).path == str(source)
    ):
        raise RuntimeError(
            "Stories needs an explicit VCS install or editable checkout identity"
        )
    command(str(cli), "manifest")
    args.state.mkdir(parents=True, exist_ok=False)
    args.fixture.mkdir(parents=True, exist_ok=False)
    (args.state / "workspace").mkdir()
    scripts = Path(__file__).resolve().parent
    provider = args.fixture / "provider/amplifier_module_provider_stories_acceptance"
    provider.mkdir(parents=True)
    shutil.copy2(scripts / "provider.py", provider / "__init__.py")
    bundle = args.fixture / "bundle"
    bundle.mkdir()
    (bundle / "bundle.md").write_text(f"""---
bundle:
  name: stories-joined-acceptance
  version: 0.0.1
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-work@{WORK_REVISION}#subdirectory=bundle.md
providers:
  - module: provider-stories-acceptance
    source: {json.dumps(str(provider.parent))}
    config:
      default_model: synthetic
---
Synthetic Stories acceptance only. No model or speech provider calls.
""")
    env = dict(
        os.environ,
        STORIES_STATE=str(args.state.resolve()),
        STORIES_URL=args.url,
        STORIES_TOKEN_FILE=str(args.token_file.resolve()),
        STORIES_FIXTURE=str(args.fixture.resolve()),
        STORIES_SOURCE=str(source),
        STORIES_SERVER="stories-acceptance-" + args.state.name,
        STORIES_PLAYWRIGHT=str(args.playwright.resolve()),
    )

    def read_json(endpoint):
        request = Request(
            args.url + endpoint,
            headers={"Authorization": "Bearer " + args.token_file.read_text().strip()},
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    preflight = {
        "at": datetime.now(timezone.utc).isoformat(),
        "health": read_json("/api/health"),
        "build": read_json("/build.json"),
        "owner": args.owned_dtu,
        "storiesRevision": revision,
        "workRevision": WORK_REVISION,
        "dependencies": dependencies,
        "storiesLockSha256": hashlib.sha256(
            (source / "uv.lock").read_bytes()
        ).hexdigest(),
        "chromiumArgs": json.loads(env.get("AMPLIFIER_CHROMIUM_ARGS", "[]")),
        "python": sys.version,
        "scripts": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in scripts.iterdir()
            if path.is_file()
        },
    }
    (args.state / "preflight.json").write_text(json.dumps(preflight, indent=2))

    def import_receipts():
        with sqlite3.connect(
            args.app_db.resolve().as_uri() + "?mode=ro", uri=True
        ) as db:
            return db.execute(
                "SELECT id, value FROM smart_tool_operations WHERE json_extract(value, '$.target.id') = ? AND json_extract(value, '$.target.name') = 'stories_create_storyboard' AND json_extract(value, '$.action') IN ('smartTools.call','smartTools.appCall') ORDER BY id",
                (env["STORIES_SERVER"],),
            ).fetchall()

    start = time.monotonic()
    try:
        for stage in ("allocate.py", "import_story.py", "retained.py", "browser.mjs"):
            if stage == "browser.mjs":
                before_imports = import_receipts()
                (args.state / "imports-before.json").write_text(
                    json.dumps(
                        [
                            {
                                "id": row[0],
                                "sha256": hashlib.sha256(row[1].encode()).hexdigest(),
                                "receipt": json.loads(row[1]),
                            }
                            for row in before_imports
                        ],
                        indent=2,
                    )
                )
                if len(before_imports) != 1:
                    raise AssertionError(
                        "Expected exactly one retained import receipt before viewing"
                    )
            interpreter = str(args.node) if stage.endswith(".mjs") else sys.executable
            with (args.state / (stage + ".log")).open("w") as log:
                subprocess.run(
                    [interpreter, str(scripts / stage)],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=240,
                )
        after_imports = import_receipts()
        (args.state / "imports-after.json").write_text(
            json.dumps(
                [
                    {
                        "id": row[0],
                        "sha256": hashlib.sha256(row[1].encode()).hexdigest(),
                        "receipt": json.loads(row[1]),
                    }
                    for row in after_imports
                ],
                indent=2,
            )
        )
        if after_imports != before_imports:
            raise AssertionError(
                "Open/reconnect/reload changed the backend import receipts"
            )
        report = json.loads((args.state / "joined-report.json").read_text())
        report["backendImportReceiptsUnchanged"] = True
        report["backendImportCount"] = len(before_imports)
        original = json.loads(
            (Path(report["allocation"]["path"]) / "library-context.json").read_text()
        )
        exported = args.state / "native-export.zip"
        with zipfile.ZipFile(exported) as archive:
            names = archive.namelist()
            if (
                json.loads(archive.read("storyboard.json"))["storyboard"]["panels"]
                != original["storyboard"]["panels"]
            ):
                raise AssertionError(
                    "Native export differs from the worker's original panels"
                )
            if not {"index.html", "manifest.json"}.issubset(names):
                raise AssertionError("Incomplete exported ZIP")
        browser = json.loads((args.state / "browser-first.json").read_text())
        report.update(
            passed=True,
            elapsedSeconds=round(time.monotonic() - start, 3),
            browser=browser,
            export={
                "sha256": hashlib.sha256(exported.read_bytes()).hexdigest(),
                "bytes": exported.stat().st_size,
                "entries": names,
                "exactPanels": True,
            },
        )
        (args.state / "result.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "passed": True,
                    "evidence": str(args.state),
                    "elapsedSeconds": report["elapsedSeconds"],
                }
            )
        )
    except BaseException as exc:
        (args.state / "result.json").write_text(
            json.dumps({"passed": False, "error": str(exc)}, indent=2)
        )
        raise


if __name__ == "__main__":
    main()
