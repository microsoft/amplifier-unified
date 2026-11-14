"""Publish only the standalone scheduling package; never select an app dependency."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tomllib
import zipfile

REPOSITORY = "microsoft/amplifier-unified"
PROJECT = "components/scheduling"
RECEIPT = "scheduling-source.json"


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def selection(root, revision, *, qualification=False):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Select a full merged commit SHA.")
    if not qualification:
        run("git", "merge-base", "--is-ancestor", revision, "origin/main", cwd=root)
    project = tomllib.loads(run("git", "show", f"{revision}:{PROJECT}/pyproject.toml", cwd=root).decode())
    version = project["project"]["version"]
    if project["project"]["name"] != "amplifier-scheduling" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected the stable standalone scheduling project.")
    files = run("git", "ls-tree", "-r", "--name-only", revision, "--", PROJECT, cwd=root).decode().splitlines()
    return {"schema": 1, "repository": REPOSITORY, "revision": revision,
            "subdirectory": PROJECT, "tree": run("git", "rev-parse", f"{revision}:{PROJECT}", cwd=root).decode().strip(),
            "version": version, "tag": "scheduling-v" + version,
            "sourceFiles": {name.removeprefix(PROJECT + "/"): sha(run("git", "show", f"{revision}:{name}", cwd=root))
                            for name in files}}


def artifact_names(selected):
    stem = "amplifier_scheduling-" + selected["version"]
    return stem + "-py3-none-any.whl", stem + ".tar.gz"


def safe(name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\\" in name or any(p in {".", ".."} for p in parts):
        raise ValueError("Unsafe package member.")


def package_identity(dist, selected):
    """Bind built payload and sdist inputs to Git, not merely their version labels."""
    wheel, source = artifact_names(selected)
    expected = selected["sourceFiles"]
    runtime = {name: digest for name, digest in expected.items() if name.startswith("amplifier_scheduling/")}
    info = f"amplifier_scheduling-{selected['version']}.dist-info/"
    with zipfile.ZipFile(dist / wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel member.")
        for name in names:
            safe(name)
            if name not in runtime and not name.startswith(info):
                raise ValueError("Wheel includes a second package or non-runtime source.")
        if {name: sha(archive.read(name)) for name in names if name in runtime} != runtime:
            raise ValueError("Wheel runtime differs from selected source.")
        metadata = BytesParser().parsebytes(archive.read(info + "METADATA"))
        if metadata["Name"] != "amplifier-scheduling" or metadata["Version"] != selected["version"]:
            raise ValueError("Wheel metadata differs from selection.")
    prefix = f"amplifier_scheduling-{selected['version']}/"
    with tarfile.open(dist / source) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate source archive member.")
        observed = {}
        for member in members:
            safe(member.name)
            if (member.name != prefix.rstrip("/") and not member.name.startswith(prefix)) or not (member.isfile() or member.isdir()):
                raise ValueError("Unexpected source archive entry.")
            if member.isfile():
                name = member.name.removeprefix(prefix)
                if name in expected:
                    observed[name] = sha(archive.extractfile(member).read())
                elif name.endswith(".py"):
                    raise ValueError("Source archive contains unselected Python code.")
        required = {name: digest for name, digest in expected.items() if name != ".gitignore"}
        if observed != required:
            raise ValueError("Source archive differs from selected project/tests.")
    return {name: {"sha256": sha((dist / name).read_bytes()), "bytes": (dist / name).stat().st_size}
            for name in (wheel, source)}


def seal(root, revision, dist, *, qualification=False):
    selected = selection(root, revision, qualification=qualification)
    if set(p.name for p in dist.iterdir()) != set(artifact_names(selected)):
        raise ValueError("Seal requires only the original wheel and source archive.")
    receipt = {**selected, "artifacts": package_identity(dist, selected)}
    (dist / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n")
    (dist / "SHA256SUMS").write_text("".join(f"{sha((dist / name).read_bytes())}  {name}\n"
                                               for name in (*artifact_names(selected), RECEIPT)))
    return receipt


def verify(root, revision, dist, *, qualification=False):
    selected = selection(root, revision, qualification=qualification)
    expected = {*artifact_names(selected), RECEIPT, "SHA256SUMS"}
    if set(p.name for p in dist.iterdir()) != expected:
        raise ValueError("Unexpected release asset set.")
    receipt = json.loads((dist / RECEIPT).read_bytes())
    if receipt != {**selected, "artifacts": package_identity(dist, selected)}:
        raise ValueError("Release receipt does not match selected source and bytes.")
    sums = "".join(f"{sha((dist / name).read_bytes())}  {name}\n" for name in (*artifact_names(selected), RECEIPT))
    if (dist / "SHA256SUMS").read_text() != sums:
        raise ValueError("Release checksums differ.")
    return receipt


def api(path, *args, missing=False):
    result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/{path}", *args], capture_output=True, text=True)
    if result.returncode:
        if missing and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError("GitHub operation failed; inspect its saved state before retrying.")
    return json.loads(result.stdout)


def release_id(value, tag):
    if (not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0
            or value.get("tag_name") != tag or value.get("prerelease") is not False
            or type(value.get("draft")) is not bool or not isinstance(value.get("assets"), list)):
        raise ValueError("Unexpected existing component release.")
    return value["id"]


def read_release(value, tag):
    """A draft may be absent from tag lookup; retain the exact returned ID."""
    identity = release_id(value, tag)
    observed = api(f"releases/{identity}")
    if release_id(observed, tag) != identity:
        raise ValueError("Component release identity changed; preserve it for review.")
    return observed


def find_release(tag):
    value = api("releases/tags/" + tag, missing=True)
    if value is not None:
        return read_release(value, tag)
    # A tag-endpoint 404 does not establish that no draft exists. Exhaust the
    # authenticated listing before creating anything; failures are never empty.
    matches = []
    for page in range(1, 101):
        values = api(f"releases?per_page=100&page={page}")
        if (not isinstance(values, list) or len(values) > 100
                or any(not isinstance(item, dict) or not isinstance(item.get("tag_name"), str) for item in values)):
            raise ValueError("Unexpected component release listing.")
        matches.extend(item for item in values if item["tag_name"] == tag)
        if len(matches) > 1:
            raise ValueError("Multiple component releases match; preserve them for review.")
        if len(values) < 100:
            return read_release(matches[0], tag) if matches else None
    raise ValueError("Component release listing is incomplete; nothing will be created.")


def remote_assets(release, dist, *, complete):
    expected = {path.name: sha(path.read_bytes()) for path in dist.iterdir()}
    assets = release.get("assets", [])
    names = [asset["name"] for asset in assets]
    if len(names) != len(set(names)) or not set(names) <= set(expected) or (complete and set(names) != set(expected)):
        raise ValueError("Existing release assets differ; nothing will be overwritten.")
    for asset in assets:
        raw = run("gh", "api", f"repos/{REPOSITORY}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream")
        if sha(raw) != expected[asset["name"]]:
            raise ValueError("Existing release bytes differ; nothing will be overwritten.")
    return set(names)


def publish(root, revision, dist):
    receipt = verify(root, revision, dist)
    tag = receipt["tag"]
    ref = api("git/ref/tags/" + tag, missing=True)
    if ref is not None and (ref.get("object", {}).get("type") != "commit" or ref["object"].get("sha") != revision):
        raise ValueError("Existing component tag differs; it will not be moved.")
    release = find_release(tag)
    if release:
        if not ref:
            raise ValueError("Existing release has no bound tag; preserve it for review.")
        remote_assets(release, dist, complete=not release["draft"])
    if not ref:
        api("git/refs", "-X", "POST", "-f", "ref=refs/tags/" + tag, "-f", "sha=" + revision)
    if release and not release["draft"]:
        if api("releases/latest")["tag_name"] == tag:
            raise ValueError("Component is incorrectly selected as the app latest release.")
        return {"tag": tag, "status": "already-published-exact-bytes"}
    if not release:
        release = api("releases", "-X", "POST", "-f", "tag_name=" + tag,
                      "-f", "name=Scheduling " + receipt["version"], "-F", "draft=true", "-f", "make_latest=false",
                      "-f", "body=Standalone scheduling policy/store package. Source: " + revision +
                      ". Unified dependency selection is unchanged. Do not co-install with a Unified version that still bundles this package.")
        release = read_release(release, tag)
    if not release["draft"]:
        raise ValueError("Component draft changed during preparation; preserve it for review.")
    present = remote_assets(release, dist, complete=False)
    missing = [str(path) for path in sorted(dist.iterdir()) if path.name not in present]
    if missing:
        run("gh", "release", "upload", tag, *missing, "--repo", REPOSITORY)  # Never --clobber.
    release = read_release(release, tag)
    remote_assets(release, dist, complete=True)
    if not release["draft"]:
        raise ValueError("Component draft changed during preparation; preserve it for review.")
    ref = api("git/ref/tags/" + tag)
    if ref["object"].get("type") != "commit" or ref["object"].get("sha") != revision:
        raise ValueError("Component tag changed during preparation; preserve the draft.")
    api(f"releases/{release['id']}", "-X", "PATCH", "-F", "draft=false", "-f", "make_latest=false")
    final = read_release(release, tag)
    remote_assets(final, dist, complete=True)
    if final["draft"] or api("releases/latest")["tag_name"] == tag:
        raise ValueError("Publication did not retain the separate app release channel.")
    return {"tag": tag, "status": "published", "revision": revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "seal", "verify", "publish"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--qualification", action="store_true", help="Allow an unmerged test candidate; never publication")
    args = parser.parse_args()
    if args.operation == "plan":
        result = selection(args.root, args.revision, qualification=args.qualification)
        if args.output:
            with args.output.open("a") as output:
                for key in ("revision", "version", "tag"):
                    output.write(f"{key}={result[key]}\n")
    else:
        if args.dist is None:
            parser.error("--dist is required")
        if args.operation == "publish":
            if args.qualification:
                parser.error("A qualification candidate cannot publish")
            result = publish(args.root, args.revision, args.dist)
        else:
            result = {"seal": seal, "verify": verify}[args.operation](args.root, args.revision, args.dist, qualification=args.qualification)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
