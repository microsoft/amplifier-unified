"""Build/bind the unsigned consumer setup from one installed Windows release wheel.

Build tooling only, not target prerequisites or publication authority. The normal
release owner supplies its already-built wheel; this never rebuilds that wheel,
changes a trust store, starts the app or enables supervised updates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_bootstrap_native import consumer_plan, prepare, sources

REPORT = "windows-consumer-release.json"
PLAN = "windows-consumer-plan.json"
SUMS = "WINDOWS_SHA256SUMS"

# The selected installation owns archive/RECORD and installed graph observation.
# Run isolated from the checkout, without editing its installed source or receipts.
INSTALLED_PROBE = r'''
import json,platform,sys
from pathlib import Path
from amplifier_web.manual_app_handoff import _payload
from amplifier_web.app_component_graph import GRAPH_PROBE,normalized
from amplifier_web.app_release_artifacts import source_revision
from importlib import metadata
import subprocess
wheel,version,revision,digest=sys.argv[1:]
d=metadata.distribution('amplifier-unified')
assert d.version==version and source_revision(d)==revision
assert Path(d.locate_file('amplifier_web')).resolve().is_relative_to(Path(sys.prefix).resolve())
a={'url':'https://github.com/microsoft/amplifier-unified/releases/download/v'+version+'/'+Path(wheel).name,
   'sha256':digest,'path':wheel}
_payload({'artifact':a,'binding':{'version':version,'revision':revision}})
p=subprocess.run([sys.executable,'-I','-c',GRAPH_PROBE],capture_output=True,text=True,check=True,timeout=30,
                 creationflags=subprocess.CREATE_NO_WINDOW)
graph=normalized(json.loads(p.stdout),artifact=a)
assert sys.platform=='win32' and sys.version_info[:2]==(3,13) and platform.machine().lower() in ('amd64','x86_64')
print(json.dumps({'python':platform.python_version(),'graph':graph,'payloadVerified':True}))
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def names(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("A stable selected release version is required.")
    return f"AmplifierUnifiedSetup-{version}-windows-x64.exe"


def wheel_identity(wheel, revision):
    if not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{40}", revision):
        raise ValueError("An exact selected source revision is required.")
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError("The selected wheel has corrupt payload bytes.")
        stamp = json.loads(archive.read("amplifier_web/release-source.json"))
    if (not isinstance(stamp, dict) or set(stamp) != {"schema", "repository", "version", "revision"}
            or type(stamp["schema"]) is not int or stamp["schema"] != 1
            or stamp["repository"] != "microsoft/amplifier-unified" or stamp["revision"] != revision):
        raise ValueError("The wheel source does not match the selected release.")
    names(stamp["version"])
    if Path(wheel).name != f"amplifier_unified-{stamp['version']}-py3-none-any.whl":
        raise ValueError("The wheel filename differs from its selected version.")
    return stamp


def build(wheel, python, revision, output):
    if sys.platform != "win32":
        raise ValueError("Consumer setup compilation requires the Windows build runner.")
    wheel, python, output = Path(wheel).resolve(), Path(python).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Build output must be absent; retain earlier artifacts.")
    stamp = wheel_identity(wheel, revision)
    result = subprocess.run([str(python), "-I", "-B", "-c", INSTALLED_PROBE, str(wheel),
                             stamp["version"], revision, sha(wheel)],
                            cwd=wheel.parent, capture_output=True, text=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    installed = json.loads(result.stdout)
    if installed.get("payloadVerified") is not True:
        raise ValueError("Installed payload verification did not complete.")
    plan = {"schema": 1, "kind": "windows-consumer-preview", "source": revision,
            "version": stamp["version"], "python": installed["python"],
            "artifact": {"url": f"https://github.com/microsoft/amplifier-unified/releases/download/v{stamp['version']}/{wheel.name}",
                         "sha256": sha(wheel), "bytes": wheel.stat().st_size}, "graph": installed["graph"]}
    raw_plan = consumer_plan(plan)
    output.mkdir(parents=True)
    (output / PLAN).write_bytes(raw_plan)
    inputs = prepare(Path(__file__).resolve().parent, output / "build", consumer=plan)
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    executable = output / names(stamp["version"])
    subprocess.run([str(compiler), *inputs["compilerOptions"], "/out:" + str(executable),
                    *["/reference:" + name for name in inputs["references"]],
                    *[str(output / "build" / name) for name in inputs["sources"]]],
                   capture_output=True, text=True, timeout=120, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    report = {"schema": 1, "source": revision, "version": stamp["version"],
              "wheelSha256": sha(wheel), "planSha256": sha(output / PLAN),
              "exe": {"name": executable.name, "bytes": executable.stat().st_size, "sha256": sha(executable)},
              "buildInputs": inputs["sources"], "installedPayloadVerified": True,
              "signed": False, "publicAcceptance": False}
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    (output / SUMS).write_text("".join(f"{sha(path)}  {path.name}\n" for path in
                                      [executable, output / PLAN, output / REPORT]))
    verify(output, wheel, revision, stamp["version"])
    return report


def verify(directory, wheel, revision, version, *, script_dir=None):
    """Bind retained executable/plan to the exact package before publication."""
    directory, wheel = Path(directory), Path(wheel)
    stamp = wheel_identity(wheel, revision)
    if stamp["version"] != version:
        raise ValueError("Consumer artifact version differs from the selected package.")
    report = json.loads((directory / REPORT).read_bytes())
    if (not isinstance(report, dict) or set(report) != {
            "schema", "source", "version", "wheelSha256", "planSha256", "exe", "buildInputs",
            "installedPayloadVerified", "signed", "publicAcceptance"}
            or type(report["schema"]) is not int or report["schema"] != 1
            or report["source"] != revision or report["version"] != version
            or report["wheelSha256"] != sha(wheel) or report["installedPayloadVerified"] is not True
            or report["signed"] is not False or report["publicAcceptance"] is not False):
        raise ValueError("Consumer build receipt differs from the selected package.")
    plan_path = directory / PLAN
    plan = json.loads(plan_path.read_bytes())
    canonical = consumer_plan(plan)
    if (plan_path.read_bytes() != canonical or report["planSha256"] != sha(plan_path)
            or plan["source"] != revision or plan["version"] != version
            or plan["artifact"]["sha256"] != sha(wheel)
            or plan["artifact"]["bytes"] != wheel.stat().st_size):
        raise ValueError("Consumer build plan differs from the selected wheel.")
    selected = sources(Path(script_dir or Path(__file__).resolve().parent), consumer=plan)
    expected_inputs = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                       for name, raw in selected.items()}
    if report["buildInputs"] != expected_inputs:
        raise ValueError("Consumer compiler inputs differ from the selected source.")
    expected = names(version)
    record = report["exe"]
    path = directory / expected
    if (not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}
            or record["name"] != expected or type(record["bytes"]) is not int
            or not 0 < record["bytes"] <= 16 * 1024 * 1024 or path.is_symlink()
            or path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]
            or path.read_bytes()[:2] != b"MZ"):
        raise ValueError("Consumer setup bytes differ from the original build.")
    assets = [path, plan_path, directory / REPORT]
    expected_sums = "".join(f"{sha(item)}  {item.name}\n" for item in assets)
    if (directory / SUMS).read_text() != expected_sums:
        raise ValueError("Consumer checksums differ from the original build.")
    return [*assets, directory / SUMS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "verify"])
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        if args.python is None:
            parser.error("build requires the selected installed Windows --python")
        print(json.dumps(build(args.wheel, args.python, args.revision, args.output)))
    else:
        if args.version is None:
            parser.error("verify requires --version")
        print(json.dumps([str(p) for p in verify(args.output, args.wheel, args.revision, args.version)]))


if __name__ == "__main__":
    main()
