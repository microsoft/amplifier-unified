"""CI-only native check: the release gate reads a plan that the real producer made on Windows.

One installation of a published release wheel, made as the release job
`windows-consumer` makes it: `uv pip install <wheel>` with no pins. The real
producer `scripts/windows_consumer_release.py build` makes the plan from that
installation. Then the release tool's own command line `verify-consumer` runs
three times against that plan:

1. Evidence with the installation's commits: the command accepts the plan (a control).
2. The host resolution has Amplifier Foundation at another commit: the command refuses.
3. The runtime graph has Amplifier Foundation at another commit: the command refuses.

Stand-ins: in a release run the host resolution and the runtime graph come from
two Linux environments of that run. Here both come from this one Windows
installation. The host rows are the plan's rows. The runtime graph is the
tool's own reading of the install records.

Not shown here: a release run, the artifact downloads of its job `release`, and
that job's step condition. The plan's form comes from the published wheel, so a
change of the app's component graph reaches this check only with a newer RELEASE.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/release_qualification.py"
SOURCE = "https://github.com/microsoft/amplifier-unified"
# The publisher's release file, by its published digest. Move this block to a newer
# release when this wheel no longer installs or the producer no longer reads it.
RELEASE = {"version": "0.106.0", "sha256": "5d89c247b7ba19aa0264516036d17a26e314a17ea05de05d9683a2f84e97b94c",
           "revision": "89de72c3a635f9e000cbb049c6bca3d525ade6e6"}
FOUNDATION = "https://github.com/microsoft/amplifier-foundation"
# Any commit other than the installed one. Nothing installs it; the gate compares text.
OTHER = "e37a946b64854eb39c09a3857ce2bc366ff297e0"
STOP = " This run stops before it publishes. If a branch moved during this run, run all jobs again."


def load(path):
    spec = importlib.util.spec_from_file_location("release_qualification_under_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(tool, *arguments):
    """The release workflow's command line, with the checkout as the working directory."""
    return subprocess.run([sys.executable, str(tool), *map(str, arguments)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def evidence(tool, folder, resolved):
    """Host evidence in the form of the job `prepare`, bound by the tool's own `candidate` command."""
    folder.mkdir(parents=True)
    load(tool).write(folder / "resolution.json", {"ok": True, "resolved": resolved})
    (folder / "qualified-overrides.txt").write_text("native check\n", encoding="utf-8")
    (folder / "development.lock").write_text("native check\n", encoding="utf-8")
    result = command(tool, "candidate", "--evidence", folder)
    if result.returncode != 0:
        raise RuntimeError("The tool did not bind the candidate: " + result.stderr[-2000:])
    return result.stdout.strip().splitlines()[-1]


def receipts(tool, folder, candidate, graph):
    """The runtime graph and the receipt that binds it to the candidate."""
    module = load(tool)
    folder.mkdir(parents=True)
    snapshot = {"build": "native-check", "graph": graph}
    module.write(folder / "qualified-runtime.json", snapshot)
    module.write(folder / "runtime.json", {"schema": 1, "candidate": candidate, "lane": "runtime",
                                           "revision": module.identity(ROOT)["revision"],
                                           "runtime": module.digest(snapshot)})


def moved(rows):
    """The same rows with each Amplifier Foundation row at the other commit, in either row form."""
    result = json.loads(json.dumps(rows))
    count = 0
    for row in result:
        direct = row.get("direct") or {}
        if row.get("url") == FOUNDATION:
            row["revision"] = OTHER
            count += 1
        elif direct.get("url") == FOUNDATION and "vcs_info" in direct:
            direct["vcs_info"]["commit_id"] = OTHER
            count += 1
    if not count:
        raise RuntimeError("The rows have no Amplifier Foundation row from Git.")
    return result


def cases(work, plan_path, records, tool=TOOL):
    """Run the gate three times against one plan. Each case records what it required."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    host = [{key: value for key, value in row.items() if key != "delivery"}
            for row in plan["graph"] if row["name"] != "amplifier-unified"]
    installed = {row["name"]: row["revision"] for row in host if row.get("url")}
    name = "amplifier-foundation"
    if name not in installed:
        raise RuntimeError("The plan has no amplifier-foundation row from Git.")
    if installed[name] == OTHER:
        raise RuntimeError("The installed Amplifier Foundation commit is the fixture's other commit. Change OTHER.")
    same = evidence(tool, work / "host-same", host)
    other = evidence(tool, work / "host-other", moved(host))
    receipts(tool, work / "runtime-same", same, records)
    receipts(tool, work / "runtime-same-for-other-host", other, records)
    receipts(tool, work / "runtime-other", same, moved(records))
    first = f"The Windows setup file has {name} at {installed[name]}. "
    wanted = [
        ("control: the evidence has the installation's commits", "host-same", same, "runtime-same", None),
        ("the host resolution has Amplifier Foundation at another commit", "host-other", other,
         "runtime-same-for-other-host", first + f"This run's host resolution has it at {OTHER}." + STOP),
        ("the runtime graph has Amplifier Foundation at another commit", "host-same", same, "runtime-other",
         first + f"The environment of this run's runtime tests has it at {OTHER}." + STOP),
    ]
    results = []
    for label, host_folder, candidate, receipt_folder, refusal in wanted:
        result = command(tool, "verify-consumer", "--evidence", work / host_folder, "--candidate", candidate,
                         "--plan", plan_path, "--receipts", work / receipt_folder)
        lines = result.stdout.splitlines()
        if refusal is None:
            passed = (result.returncode == 0
                      and all(any(line.startswith(f"{item} {commit}: the same commit") for line in lines)
                              for item, commit in installed.items())
                      and lines[-1:] == [f"{len(installed)} Git components of the Windows setup file "
                                         "were compared. None differs."])
        else:
            passed = result.returncode != 0 and refusal in result.stderr
        results.append({"case": label, "required": "accepted" if refusal is None else "refused: " + refusal,
                        "returncode": result.returncode, "passed": passed,
                        "output": (result.stdout + result.stderr)[-3000:]})
    return {"gitComponents": installed, "cases": results, "ok": all(item["passed"] is True for item in results)}


def run(*arguments, **options):
    result = subprocess.run([*map(str, arguments)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", **options)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(str(arguments[0])).name} {arguments[1]} failed: "
                           + (result.stdout + result.stderr)[-3000:])


def prepare(work):
    """One installation, as the release job makes it, and the real producer's plan of it."""
    assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32
    uv = shutil.which("uv")
    assert uv
    wheel = work / f"amplifier_unified-{RELEASE['version']}-py3-none-any.whl"
    with urllib.request.urlopen(f"{SOURCE}/releases/download/v{RELEASE['version']}/{wheel.name}",
                                timeout=120) as response:
        raw = response.read(64 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != RELEASE["sha256"]:
        raise RuntimeError("The downloaded wheel is not the published release file.")
    wheel.write_bytes(raw)
    environment = work / "windows-consumer-app"
    python = environment / "Scripts/python.exe"
    run(uv, "venv", "--python", "3.13", environment, timeout=300)
    run(uv, "pip", "install", "--python", python, wheel, timeout=900)
    run(uv, "pip", "check", "--python", python, timeout=120)
    run(sys.executable, ROOT / "scripts/windows_consumer_release.py", "build", "--wheel", wheel,
        "--python", python, "--revision", RELEASE["revision"], "--output", work / "windows-consumer",
        cwd=ROOT, timeout=600)
    return work / "windows-consumer/windows-consumer-plan.json", load(TOOL).graph(python)


def main():
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve()
    folder, work = temporary / "consumer-plan-evidence", temporary / "consumer-plan-work"
    report = {"release": RELEASE, "stage": "installation", "ok": False}
    try:
        work.mkdir()
        plan_path, records = prepare(work)
        raw = plan_path.read_bytes()
        plan = json.loads(raw)
        assert plan["version"] == RELEASE["version"] and plan["source"] == RELEASE["revision"]
        # The control must cover a component that is in a subdirectory of its repository.
        assert any(row.get("subdirectory") for row in plan["graph"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / plan_path.name).write_bytes(raw)
        report.update(stage="cases", planSha256=hashlib.sha256(raw).hexdigest(), packages=len(plan["graph"]))
        report.update(cases(work / "cases", plan_path, records))
        report["stage"] = "complete"
    except Exception as error:  # the report must say where the check ended
        report.update(ok=False, error=f"{type(error).__name__}: {error}"[:4000])
    finally:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] is True and report["stage"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
