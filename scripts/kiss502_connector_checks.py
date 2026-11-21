"""Pinned, ordinary connector fixtures; no DTU launcher or live credentials.

Parent guest:
  python scripts/kiss502_connector_checks.py prepare --output /artifacts/connectors
  python scripts/kiss502_connector_checks.py check --output /artifacts/connectors

Local preflight may prepare inputs and run check --minecraft-only. Full check
requires the parent's prepared test contract and /artifacts output. It starts only
the maintained disposable loopback fixtures, never an installed service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request


PINS = {
    "slack": ("https://github.com/microsoft/amplifier-acp-slack.git",
              "6f7db06305a8ba562b0d7b0a2d7ee340ed9f3190",
              "329fc57399dc44a4ecfe69bcbae8f786c40b6c88"),
    "minecraft": ("https://github.com/microsoft/amplifier-acp-minecraft.git",
                  "3097280413410b80c908a2ef9d5f5ddff37c36a3",
                  "c74f562092c3f7861c60ef49209d7fc8b747274d"),
}
SDK_COMMIT = "9853d8b514d6340000bcbd01ec07808436db80cf"
SLACK_RELEASE = "845ed2c1389dd3fbb5b3601c7fb8c6bab2f5bdc3"
SLACK_URL = ("https://github.com/microsoft/amplifier-acp-slack/releases/download/"
             "v0.18.3/acp-slack-adapter-0.18.3-aarch64-unknown-linux-gnu.tar.gz")
ARCHIVE_HASH = "cf0226887f4408e0b24aba350c74c5866d7fa75870a677fdf87186a7e44bf65e"
MANIFEST_HASH = "b5371b7e7d4ce3d03eaca52aebfd5a7811fb1e1af9a525e202ca2a3f712676ac"
BINARY_HASH = "d837885756191f2f0a41daeabfa44e7c9d6b17d67dc92f7a86c8de32ea076f1e"
MC_TESTS = ("test_sdk_boundary", "test_session_resume", "test_reconnect")
SLACK_TESTS = ("process_integration.py", "scoped_work.py", "slack_huddles.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def source_inventory(source, name):
    url, commit, tree = PINS[name]
    require(git(source, "rev-parse", "HEAD") == commit, f"{name}: wrong commit")
    require(git(source, "rev-parse", "HEAD^{tree}") == tree, f"{name}: wrong tree")
    require(git(source, "remote", "get-url", "origin") == url, f"{name}: wrong origin")
    require(not git(source, "diff", "HEAD", "--"), f"{name}: tracked changes")
    paths = git(source, "ls-files", "-z").rstrip("\0").split("\0")
    files = {}
    for relative in paths:
        path = source / relative
        require(path.is_file() and not path.is_symlink(), f"Nonregular input: {relative}")
        files[relative] = digest(path)
    return {"url": url, "commit": commit, "tree": tree, "files": files}


def fixture_env(output):
    """No inherited tokens, user configuration or optional endpoint overrides."""
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    env.update(HOME=str(output / "home"), TMPDIR=str(output / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               XDG_CONFIG_HOME=str(output / "home/config"),
               XDG_CACHE_HOME=str(output / "home/cache"),
               WORKSHOP_TEST_PYTHON=str(output / "minecraft-venv/bin/python"))
    return env


def command(output, name, argv, *, cwd=None, env=None):
    """Normal project runners, with original failure logs and no silent retries."""
    log = output / (name + ".log")
    require(not log.exists(), f"Existing stage {name}; inspect saved result, use a fresh output")
    start = time.time()
    with log.open("x") as stream:
        result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    receipt = {"argv": list(map(str, argv)), "cwd": str(cwd) if cwd else None,
               "returncode": result.returncode, "startedAt": start,
               "seconds": time.time() - start, "logSha256": digest(log)}
    write(output / (name + ".json"), receipt)
    print(f"{name}: exit {result.returncode}", flush=True)
    return result.returncode


def download(url, path, expected, limit):
    with urllib.request.urlopen(url, timeout=120) as response, path.open("xb") as target:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            require(count <= limit, "Download exceeds selected size")
            target.write(chunk)
    require(digest(path) == expected, f"Hash mismatch: {path}")


def validate_members(members):
    require(len(members) <= 5000, "Too many archive entries")
    require(sum(row.size for row in members) <= 500_000_000, "Archive too large")
    seen = set()
    for row in members:
        path = PurePosixPath(row.name)
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in row.name,
                f"Unsafe archive path: {row.name}")
        require(row.isfile() or row.isdir(), f"Nonregular archive entry: {row.name}")
        require(row.name not in seen, f"Duplicate archive entry: {row.name}")
        seen.add(row.name)
        require(path.parts and (path.parts[0] in {
            "LICENSE", "START-HERE.txt", "native-bundle.json", "acp-slack-adapter", "runtime"}),
                f"Unexpected package entry: {row.name}")


def package_inventory(folder):
    files = {}
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink(), f"Package link: {path}")
        if path.is_file():
            files[str(path.relative_to(folder))] = digest(path)
        else:
            require(path.is_dir(), f"Nonregular package entry: {path}")
    return files


def verify_package(output):
    saved = json.loads((output / "slack-artifact.json").read_text())
    require(digest(output / "slack-release-manifest.json") == MANIFEST_HASH,
            "Slack published manifest drift")
    require(digest(output / "slack.tar.gz") == ARCHIVE_HASH, "Slack archive drift")
    require(package_inventory(output / "slack-package") == saved["payload"],
            "Slack extracted payload drift")
    require(saved["payload"]["acp-slack-adapter"] == BINARY_HASH, "Slack binary drift")


def slack_package(output, archive=None):
    target = output / "slack.tar.gz"
    if archive:
        shutil.copyfile(archive, target)
    else:
        download(SLACK_URL, target, ARCHIVE_HASH, 137_513_257)
    require(target.stat().st_size == 137_513_257 and digest(target) == ARCHIVE_HASH,
            "Slack archive size/hash mismatch")
    manifest = output / "slack-release-manifest.json"
    download(SLACK_URL + ".json", manifest, MANIFEST_HASH, 10_000)
    published = json.loads(manifest.read_text())
    require(published["sha256"] == ARCHIVE_HASH and
            published["binary_sha256"] == BINARY_HASH, "Published identity mismatch")
    folder = output / "slack-package"
    folder.mkdir()
    with tarfile.open(target) as source:
        validate_members(source.getmembers())
        source.extractall(folder, filter="data")
    binary = folder / "acp-slack-adapter"
    require(digest(binary) == BINARY_HASH, "Slack binary mismatch")
    with binary.open("rb") as stream:
        header = stream.read(20)
    require(header[:5] == b"\x7fELF\x02" and header[18:20] == b"\xb7\x00",
            "Expected aarch64 ELF64")
    native = json.loads((folder / "native-bundle.json").read_text())
    require(native["version"] == "0.18.3" and native["target"] == published["target"],
            "Native package identity mismatch")
    payload = package_inventory(folder)
    for path, expected in native["python_files"].items():
        require(payload.get("runtime/python/" + path) == expected, f"Python payload: {path}")
    write(output / "slack-artifact.json", {
        "url": SLACK_URL, "archiveSha256": ARCHIVE_HASH, "binarySha256": BINARY_HASH,
        "manifestSha256": digest(manifest), "payload": payload,
        "qualification": "Published artifact; not rebuilt at selected source commit"})


def prepare(output, archive=None):
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("home", "tmp", "sources"):
        (output / folder).mkdir(mode=0o700)
    env = fixture_env(output)
    for name, (url, commit, _) in PINS.items():
        source = output / "sources" / name
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "remote", "add", "origin", url], check=True)
        require(command(output, name + "-fetch", ["git", "-C", source, "fetch",
                "--depth=1", "origin", commit]) == 0, f"{name} source fetch failed")
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
                       check=True, capture_output=True)
        write(output / (name + "-source.json"), source_inventory(source, name))
    slack = output / "sources/slack"
    require(command(output, "slack-release-fetch", ["git", "-C", slack, "fetch",
                    "--depth=1", "origin", SLACK_RELEASE]) == 0, "Release fetch failed")
    changed = git(slack, "diff", "--name-only", SLACK_RELEASE, PINS["slack"][1]).splitlines()
    require(changed == [".github/workflows/check.yml"], "Slack runtime lineage changed")
    write(output / "slack-lineage.json", {
        "selected": PINS["slack"][1], "release": SLACK_RELEASE, "changedPaths": changed,
        "diff": git(slack, "diff", SLACK_RELEASE, PINS["slack"][1])})
    slack_package(output, archive)
    for name in PINS:
        require(command(output, name + "-venv", ["uv", "venv", "--python", sys.executable,
                        output / (name + "-venv")], env=env) == 0, "Venv failed")
    require(command(output, "slack-dependencies", ["uv", "pip", "install", "--python",
                    output / "slack-venv/bin/python", "aiohttp==3.14.5"], env=env) == 0,
            "Slack dependencies failed")
    require(command(output, "minecraft-dependencies", ["uv", "pip", "install", "--python",
                    output / "minecraft-venv/bin/python", "-r",
                    output / "sources/minecraft/requirements.txt"], env=env) == 0,
            "Minecraft dependencies failed")
    for name in PINS:
        require(command(output, name + "-graph", ["uv", "pip", "freeze", "--python",
                        output / (name + "-venv/bin/python")], env=env) == 0, "Graph read failed")
    require(command(output, "minecraft-npm-ci",
                    ["npm", "ci", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund"],
                    cwd=output / "sources/minecraft", env=env) == 0, "npm ci failed")
    write(output / "prepared.json", {"scriptSha256": digest(__file__),
          "python": sys.version, "node": subprocess.check_output(["node", "--version"], text=True).strip(),
          "limits": ["fake platforms", "scripted ACP", "no live accounts or paid provider",
                     "no joined Unified backend asserted by maintained fixture passes"]})


def check(output, minecraft_only=False):
    require((output / "prepared.json").is_file(), "Prepare first")
    if not minecraft_only:
        require(platform.system() == "Linux" and platform.machine() == "aarch64",
                "Selected Slack release is Linux ARM64")
        require(output.is_relative_to(Path("/artifacts")) and
                os.environ.get("AMPLIFIER_TEST_CONTRACT"),
                "Full platform check belongs to parent's prepared guest under /artifacts")
    for name in PINS:
        actual = source_inventory(output / "sources" / name, name)
        require(actual == json.loads((output / (name + "-source.json")).read_text()),
                f"{name} input drift")
    env = fixture_env(output)
    mc = output / "sources/minecraft"
    python = output / "minecraft-venv/bin/python"
    # Verify the connector's separate old SDK, not Unified's independently versioned SDK.
    probe = ("import importlib.metadata as m,json; d=m.distribution('agent-client-protocol'); "
             "u=json.loads(d.read_text('direct_url.json')); "
             f"assert u['vcs_info']['commit_id']=={SDK_COMMIT!r}; "
             "assert d.version=='0.12.1'; print(json.dumps(u))")
    require(command(output, "minecraft-sdk-provenance", [python, "-I", "-B", "-c", probe],
                    env=env) == 0, "Wrong SDK graph")
    statuses = {}
    for name in MC_TESTS:
        statuses[name] = command(output, name, [python, "-B", "-m", "unittest",
                                 "discover", "-s", "tests", "-p", name + ".py", "-v"],
                                 cwd=mc, env=env)
    statuses["body-chat"] = command(output, "body-chat",
                                   ["node", "--test", "tests/body-chat.test.cjs"], cwd=mc, env=env)
    if not minecraft_only:
        binary = output / "slack-package/acp-slack-adapter"
        verify_package(output)
        env["ACP_SLACK_TEST_BINARY"] = str(binary)
        env["SLACK_HUDDLE_RESULT_DIR"] = str(output / "slack-huddles")
        for name in SLACK_TESTS:
            statuses[name] = command(output, name.removesuffix(".py"),
                                     [output / "slack-venv/bin/python", "-B", "tests/" + name],
                                     cwd=output / "sources/slack", env=env)
        verify_package(output)
    for name in PINS:
        require(source_inventory(output / "sources" / name, name) ==
                json.loads((output / (name + "-source.json")).read_text()),
                f"{name} changed during fixtures")
    write(output / "result.json", {"stages": statuses, "minecraftOnly": minecraft_only,
          "executionScriptSha256": digest(__file__),
          "passed": all(code == 0 for code in statuses.values()),
          "joinedUnifiedBackend": "not_run",
          "limits": ["No real Slack authorization/delivery, Mineflayer world, or model judgment",
                     "Maintained fixtures use scripted ACP peers and fake platform state"]})
    return 0 if all(code == 0 for code in statuses.values()) else 1


def joined(output, python, test):
    require(output.is_relative_to(Path("/artifacts")) and
            os.environ.get("AMPLIFIER_TEST_CONTRACT"), "Parent's prepared guest only")
    require(test.is_file() and test.name == "kiss502_connector_native.py", "Select joined fixture")
    require((test.parent / "peer_return_worker_probe.py").is_file() and
            (test.parent / "acp_native_provider.py").is_file(), "Copy the two maintained native helpers")
    verify_package(output)
    for name in PINS:
        require(source_inventory(output / "sources" / name, name) ==
                json.loads((output / (name + "-source.json")).read_text()), f"{name} source drift")
    env = fixture_env(output)
    env.update(KISS502_CONNECTORS=str(output),
               AMPLIFIER_TEST_CONTRACT=os.environ["AMPLIFIER_TEST_CONTRACT"])
    result = command(output, "joined", [
        python, "-I", "-B", "-m", "pytest", "-q", "-c", "/dev/null", "--noconftest",
        "-p", "aiohttp.pytest_plugin", "--asyncio-mode=auto",
        "--basetemp=" + str(output / "joined"), "--junitxml=" + str(output / "joined.xml"), test],
        cwd=output, env=env)
    verify_package(output)
    write(output / "joined-result.json", {
        "returncode": result, "fixtureSha256": digest(test),
        "scriptSha256": digest(__file__), "qualification": "Pass only if joined pytest exits zero",
        "limits": ["Two direct-backend cases, not an Instance/full Work/live platform campaign"]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "joined"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slack-archive", type=Path)
    parser.add_argument("--minecraft-only", action="store_true")
    parser.add_argument("--backend-python", type=Path)
    parser.add_argument("--joined-test", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action == "prepare":
        prepare(output, args.slack_archive)
        return 0
    if args.action == "joined":
        require(args.backend_python and args.joined_test, "Select installed backend Python and copied joined test")
        # Resolving a venv's Python symlink selects the base interpreter and loses
        # the parent's installed backend graph.
        return joined(output, args.backend_python.absolute(), args.joined_test.resolve())
    return check(output, args.minecraft_only)


if __name__ == "__main__":
    raise SystemExit(main())