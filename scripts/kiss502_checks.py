"""Finite KISS502 installed checks using the ordinary managed browser profile.

No environment launcher, alternate test runner, credentials or model calls.
Preflight names every copied test input before guest admission. Run only inside
the task's prepared guest; logs/wheel/JUnit stay on the artifacts volume.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "test_acp_native_execution", "test_acp_cli", "test_acp_participant",
    "test_acp_participation", "test_shared_conversation", "test_room_context",
    "test_external_conversation", "test_acp_approvals", "test_acp_retirement",
    "test_coordination_revision_native", "test_coordination_revisions",
    "test_coordination_delivery", "test_coordination_return_policy",
    "test_coordination_superseded_returns", "test_coordination_wait_transport",
    "test_peer_return_workflow",
)
FIXTURES = (
    "acp_native_provider.py", "external_acp_agent.py", "external_acp_participation.py",
    "shared_acp_agent.py", "room_context_acp_agent.py", "retiring_acp_agent.py",
    "peer_return_worker_probe.py", "collaboration_native_probe.py",
    "external_multiparty_ui_server.py",
)
BROWSERS = ("external-multiparty-browser.mjs", "external-approval-browser.mjs")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selection():
    """Only local test imports are traversed; product source is never copied."""
    pending = [ROOT / "tests" / (name + ".py") for name in TARGETS]
    pending += [ROOT / "tests" / "fixtures" / name for name in FIXTURES]
    pending += [ROOT / "tests/conftest.py"]
    selected = set()
    while pending:
        path = pending.pop()
        if path in selected:
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular test input: {path}")
        selected.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            modules = ([node.module] if isinstance(node, ast.ImportFrom) else
                       [row.name for row in node.names] if isinstance(node, ast.Import) else [])
            for module in filter(None, modules):
                for base in (ROOT / "tests", path.parent):
                    candidate = base / (module.replace(".", "/") + ".py")
                    if candidate.is_file() and candidate.is_relative_to(ROOT / "tests"):
                        pending.append(candidate)
    selected |= {ROOT / "tests/web" / name for name in (*BROWSERS, "fixture-python.mjs")}
    for path in selected:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular test input: {path}")
    return {str(p.relative_to(ROOT)): {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(selected)}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


PROBE = r"""
import base64,hashlib,importlib,importlib.metadata as md,json,pathlib,sys,zipfile
wheel,source=map(pathlib.Path,sys.argv[1:])
modules={}
for name in ('amplifier_web','amplifier_web.acp_server','amplifier_web.runtime_worker',
             'amplifier_web.collaboration','amplifier_web.acp_participant',
             'amplifier_core','amplifier_module_loop_live.orchestrator','amplifier_module_context_simple'):
 p=pathlib.Path(importlib.import_module(name).__file__).resolve()
 assert 'site-packages' in p.parts and not p.is_relative_to(source),(name,p)
 modules[name]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
dist=md.distribution('amplifier-unified');verified={}
for item in dist.files or []:
 if item.hash and item.hash.mode=='sha256':
  p=pathlib.Path(dist.locate_file(item))
  raw=hashlib.sha256(p.read_bytes()).digest()
  assert base64.urlsafe_b64encode(raw).decode().rstrip('=')==item.hash.value,str(item)
  verified[str(item)]=raw.hex()
with zipfile.ZipFile(wheel) as z:
 payload={}
 for name in z.namelist():
  if name.endswith('/') or '.dist-info/' in name:continue
  expected=hashlib.sha256(z.read(name)).hexdigest()
  assert hashlib.sha256(pathlib.Path(dist.locate_file(name)).read_bytes()).hexdigest()==expected,name
  payload[name]=expected
for p in (source/'amplifier_web').rglob('*.py'):
 if '__pycache__' in p.parts:continue
 name=str(p.relative_to(source))
 assert name in payload and hashlib.sha256(p.read_bytes()).hexdigest()==payload[name],name
graph=sorted([{'name':d.metadata['Name'],'version':d.version,
 'direct':json.loads(d.read_text('direct_url.json') or 'null')} for d in md.distributions()],key=lambda x:x['name'])
print(json.dumps({'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
 'modules':modules,'record':verified,'payload':payload,'distributions':graph},indent=2))
"""


def run(output):
    if ROOT != Path("/workspace") or not os.environ.get("AMPLIFIER_TEST_CONTRACT"):
        raise ValueError("Run only in the task-owned prepared guest, never a serving installation")
    output.mkdir(parents=True, exist_ok=False)
    inputs = selection()
    write(output / "inputs.json", inputs)
    python = os.environ["AMPLIFIER_TEST_PYTHON"]
    clean = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""}
    clean.pop("VIRTUAL_ENV", None)
    clean["TMPDIR"] = str(output / "tmp")
    Path(clean["TMPDIR"]).mkdir()
    stages = []

    def command(name, argv, cwd=ROOT, env=clean):
        with (output / (name + ".log")).open("w") as log:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=log,
                                    stderr=subprocess.STDOUT)
        stages.append({"name": name, "argv": argv, "cwd": str(cwd), "returncode": result.returncode})
        write(output / "stages.json", stages)
        if result.returncode:
            raise RuntimeError(f"{name} failed; retained log and stages.json")

    command("build", ["/opt/unified-prepared/build-venv/bin/python", "-I", "-B",
                      "-m", "build", "--wheel", "--no-isolation", "--outdir",
                      str(output / "dist"), str(ROOT)])
    wheel, = (output / "dist").glob("*.whl")
    command("install", ["uv", "pip", "install", "--python", python,
                        "--no-deps", "--reinstall", str(wheel)])
    harness = output / "harness"
    harness.mkdir()
    for name in inputs:
        target = harness / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        assert digest(target) == inputs[name]["sha256"]
    assert not (harness / "amplifier_web").exists()
    (harness / "tests/web/node_modules").symlink_to(ROOT / "tests/web/node_modules", target_is_directory=True)
    command("installed-before", [python, "-I", "-B", "-c", PROBE, str(wheel), str(ROOT)], cwd=harness)
    command("pytest", [python, "-I", "-B", "-m", "pytest", "-c", "/dev/null",
                      "-o", "asyncio_mode=auto", "-q", "--basetemp", str(output / "pytest-tmp"),
                      "--junitxml=" + str(output / "junit.xml"),
                      *["tests/" + name + ".py" for name in TARGETS]], cwd=harness,
            env={**clean, "AMPLIFIER_TEST_ARTIFACTS": str(output / "native")})
    for name in BROWSERS:
        command(name, ["node", "tests/web/" + name], cwd=harness, env={
            **clean, "AMPLIFIER_TEST_ROOT": str(harness), "AMPLIFIER_TEST_PYTHON": python,
            "AMPLIFIER_TEST_ARTIFACTS": str(output / name.removesuffix(".mjs"))})
    command("installed-after", [python, "-I", "-B", "-c", PROBE, str(wheel), str(ROOT)], cwd=harness)
    assert (output / "installed-before.log").read_bytes() == (output / "installed-after.log").read_bytes()
    write(output / "summary.json", {"status": "passed", "stages": stages,
                                   "inputs": len(inputs), "wheelSha256": digest(wheel),
                                   "scope": "Finite installed host/SDK/native Core/browser cases; no live services or paid API"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "run"))
    parser.add_argument("--output", type=Path, default=Path("/artifacts/kiss502"))
    args = parser.parse_args()
    if args.operation == "preflight":
        print(json.dumps({"files": selection(), "targets": TARGETS, "browsers": BROWSERS}, indent=2))
    else:
        run(args.output)