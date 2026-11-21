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
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "test_acp_native_execution", "test_acp_cli", "test_acp_participant",
    "test_acp_participation", "test_shared_conversation", "test_room_context",
    "test_external_conversation", "test_acp_approvals", "test_acp_retirement",
    "test_coordination_revision_native", "test_coordination_revisions",
    "test_coordination_delivery", "test_coordination_return_policy",
    "test_coordination_superseded_returns", "test_coordination_wait_transport",
    "test_peer_return_workflow",
    "test_agent_instance",
    "test_agent_instance_composition",
    "test_owner_update_wait",
    "test_agent_instance_native_lifecycle",
    "test_app_guidance", "test_collaborative_workspaces", "test_interrupted_peer_recovery",
    "test_peer_resume", "test_question_peer_resume", "test_result_artifact_save",
    "test_result_artifacts", "test_resume_handoff", "test_external_multiparty",
    "test_acp_server",
)
FIXTURES = (
    "acp_native_provider.py", "acp_lifecycle_provider.py", "external_acp_agent.py", "external_acp_participation.py",
    "shared_acp_agent.py", "room_context_acp_agent.py", "retiring_acp_agent.py",
    "peer_return_worker_probe.py", "collaboration_native_probe.py",
    "external_multiparty_ui_server.py",
    "canvas_runtime_probe.py", "surface_context_probe.py", "workspace_awareness_runtime_probe.py",
)
BROWSERS = ("external-multiparty-browser.mjs", "external-approval-browser.mjs")
OWNER_TARGETS = ("test_execution_owner", "test_execution_owner_service",
                 "test_execution_owner_resume", "test_task_continuity", "test_work_native_effects")
CHANGED_TARGETS = ("test_acp_native_execution", "test_agent_instance",
                   "test_agent_instance_composition", "test_external_conversation",
                   "test_shared_conversation", "test_acp_participant", "test_owner_update_wait")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selection():
    """Only local test imports are traversed; product source is never copied."""
    pending = [ROOT / "tests" / (name + ".py") for name in (*TARGETS, *OWNER_TARGETS)]
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
    pending_js = [ROOT / "tests/web" / name for name in BROWSERS]
    while pending_js:
        path = pending_js.pop()
        if path in selected:
            continue
        selected.add(path)
        # Maintained browser helpers use literal sibling ES module imports.
        import re
        for name in re.findall(r"""from\s+['"](\./[^'"]+)['"]""", path.read_text()):
            pending_js.append(path.parent / name)
    for path in selected:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular test input: {path}")
    return {str(p.relative_to(ROOT)): {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(selected)}


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def published_instance(output):
    """Fetch one inspected public artifact, with its immutable release hash."""
    url = ("https://github.com/microsoft/amplifier-constellation-instance/releases/"
           "download/v0.22.15/agent-instance-aarch64-unknown-linux-gnu.tar.gz")
    archive = output / "instance.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as incoming, archive.open("xb") as target:
        count = 0
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            count += len(chunk)
            if count > 10 * 1024 * 1024:
                raise ValueError("Instance artifact exceeds the selected size")
            target.write(chunk)
    assert digest(archive) == "ddc6304b2610eb89ff0067532e9d83c44fc0ae2a8692003fe06af4c5a65bdd34"
    folder = output / "instance"
    folder.mkdir()
    with tarfile.open(archive) as source:
        assert set(source.getnames()) == {"agent-instance", "build-info.json", "LICENSE", "THIRD-PARTY-NOTICES.txt"}
        assert all(row.isfile() for row in source.getmembers())
        source.extractall(folder, filter="data")
    binary = folder / "agent-instance"
    assert digest(binary) == "8772fc97435eb9922bed694b9e0d4ef43d5312c0ee036ff0173b9a27a18afbfa"
    build = json.loads((folder / "build-info.json").read_text())
    assert build["commit"] == "c2ca117029a02bc6c22cdb382b29d49a73e99f95"
    write(output / "instance-artifact.json", {"url": url, "archiveSha256": digest(archive),
                                           "binarySha256": digest(binary), "build": build})
    return binary


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


def run(output, targets=TARGETS):
    if ROOT != Path("/workspace") or not os.environ.get("AMPLIFIER_TEST_CONTRACT"):
        raise ValueError("Run only in the task-owned prepared guest, never a serving installation")
    output.mkdir(parents=True, exist_ok=False)
    contract = json.loads(Path(os.environ["AMPLIFIER_TEST_CONTRACT"]).read_text())
    if not contract.get("pythonBuild", {}).get("available"):
        raise ValueError("The ordinary prepared build prerequisites are unavailable; inspect preparation logs")
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("The selected published Instance artifact requires Linux ARM64")
    write(output / "execution-contract.json", contract)
    inputs = selection()
    write(output / "inputs.json", inputs)
    python = contract["python"]
    clean = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""}
    clean.pop("VIRTUAL_ENV", None)
    clean["TMPDIR"] = str(output / "tmp")
    Path(clean["TMPDIR"]).mkdir()
    stages = []

    def command(name, argv, cwd=ROOT, env=clean, required=True):
        with (output / (name + ".log")).open("w") as log:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=log,
                                    stderr=subprocess.STDOUT)
        stages.append({"name": name, "argv": argv, "cwd": str(cwd), "returncode": result.returncode})
        write(output / "stages.json", stages)
        if result.returncode and required:
            raise RuntimeError(f"{name} failed; retained log and stages.json")

    clean["KISS502_INSTANCE_BINARY"] = str(published_instance(output))
    command("build", [contract["pythonBuild"]["python"], "-I", "-B",
                      "-m", "build", "--wheel", "--no-isolation", "--outdir",
                      str(output / "dist"), str(ROOT)])
    wheel, = (output / "dist").glob("*.whl")
    # The maintained prepared runtime is read-only. Keep its locked dependencies
    # read-only and install the candidate into a fresh task-owned environment.
    prepared_python = python
    command("installed-venv", ["uv", "venv", "--python", prepared_python, str(output / "venv")])
    python = str(output / "venv/bin/python")
    dependency_site = subprocess.check_output(
        [prepared_python, "-I", "-B", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        text=True).strip()
    own_site = subprocess.check_output(
        [python, "-I", "-B", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        text=True).strip()
    (Path(own_site) / "prepared-dependencies.pth").write_text(dependency_site + "\n")
    write(output / "prepared-dependencies.json", {"readOnlySite": dependency_site,
          "preparedPython": prepared_python, "candidatePython": python,
          "method": "Explicit read-only dependency site; candidate distribution precedes it"})
    command("install", ["uv", "pip", "install", "--python", python,
                        "--target", own_site, "--no-deps", str(wheel)])
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
    if "test_app_guidance" in targets:
        command("source-census", [python, "-I", "-B", "-m", "pytest", "-c", "/dev/null",
                "-p", "no:cacheprovider", "-q",
                str(ROOT / "tests/test_app_guidance.py") + "::test_all_installers_supply_an_explicit_execution_role"],
                cwd=ROOT)
    command("pytest", [python, "-I", "-B", "-m", "pytest", "-c", "/dev/null",
                      "--rootdir", str(harness),
                      "-o", "asyncio_mode=auto", "-p", "no:cacheprovider", "-q", "--basetemp", str(output / "pytest-tmp"),
                      "--junitxml=" + str(output / "junit.xml"),
                      "--deselect=tests/test_app_guidance.py::test_all_installers_supply_an_explicit_execution_role",
                      *["tests/" + name + ".py" for name in targets]], cwd=harness,
            env={**clean, "UNIFIED_RUNTIME_PYTHON": python,
                 "AMPLIFIER_TEST_ARTIFACTS": str(output / "native")}, required=False)
    command("owner-pytest", [python, "-I", "-B", "-m", "pytest", "-c", "/dev/null",
            "--rootdir", str(harness), "-o", "asyncio_mode=auto", "-p", "no:cacheprovider", "-q",
            "--basetemp", str(output / "owner-tmp"),
            "--junitxml=" + str(output / "owner-junit.xml"),
            *["tests/" + name + ".py" for name in OWNER_TARGETS]], cwd=harness,
            env={**clean, "UNIFIED_RUNTIME_PYTHON": python}, required=False)
    for name in BROWSERS:
        command(name, ["node", "tests/web/" + name], cwd=harness, env={
            **clean, "AMPLIFIER_TEST_ROOT": str(harness), "AMPLIFIER_TEST_PYTHON": python,
            "AMPLIFIER_TEST_ARTIFACTS": str(output / name.removesuffix(".mjs"))}, required=False)
    command("installed-after", [python, "-I", "-B", "-c", PROBE, str(wheel), str(ROOT)], cwd=harness)
    assert (output / "installed-before.log").read_bytes() == (output / "installed-after.log").read_bytes()
    cases = [{"classname": row.get("classname"), "name": row.get("name"),
              "status": next((kind for kind in ("failure", "error", "skipped") if row.find(kind) is not None), "passed")}
             for row in ET.parse(output / "junit.xml").getroot().iter("testcase")]
    missing = [name for name in targets if not any(
        row["classname"].endswith(name) for row in cases)]
    passed = (all(row["returncode"] == 0 for row in stages)
              and not missing and cases and all(row["status"] == "passed" for row in cases))
    write(output / "summary.json", {"status": "passed" if passed else "failed", "stages": stages, "cases": cases,
                                   "missingRequiredModules": missing,
                                   "counts": {status: sum(row["status"] == status for row in cases)
                                              for status in ("passed", "failure", "error", "skipped")},
                                   "inputs": len(inputs), "wheelSha256": digest(wheel),
                                   "scope": "Finite installed host/SDK/native Core/browser cases; no live services or paid API"})
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "run", "changed"))
    parser.add_argument("--output", type=Path, default=Path("/artifacts/kiss502"))
    args = parser.parse_args()
    if args.operation == "preflight":
        print(json.dumps({"files": selection(), "targets": TARGETS, "browsers": BROWSERS}, indent=2))
    else:
        run(args.output, CHANGED_TARGETS if args.operation == "changed" else TARGETS)