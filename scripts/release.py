"""Build/release checks for the immutable GitHub Releases application channel."""
from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tomllib
import zipfile

VERSION = re.compile(r"\d+\.\d+\.\d+\Z")


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f"Release command failed: {args[0]} {args[1]}")
    return result.stdout.strip()


def package_version(project, module):
    version = tomllib.loads(project)["project"]["version"]
    values = [ast.literal_eval(node.value) for node in ast.parse(module).body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    if not VERSION.fullmatch(version) or values != [version]:
        raise ValueError("Release requires matching stable versions in pyproject.toml and amplifier_web/__init__.py")
    return version


def version_at(root, ref=None):
    def read(path):
        return run("git", "show", f"{ref}:{path}", cwd=root) if ref else (root / path).read_text()
    return package_version(read("pyproject.toml"), read("amplifier_web/__init__.py"))


def plan(root):
    version = version_at(root)
    tag = "v" + version
    tags = run("git", "tag", "--list", tag, cwd=root).splitlines()
    revision = run("git", "rev-parse", "HEAD", cwd=root)
    if tags:
        tagged_revision = run("git", "rev-parse", tag + "^{commit}", cwd=root)
        if version_at(root, tagged_revision) != version:
            raise ValueError("Existing immutable release tag does not match its package version")
        if tagged_revision != revision:
            raise ValueError(
                "Existing immutable release tag does not match the selected commit. "
                "Increment the package version to release this commit, or select the "
                "original tagged commit to resume that release."
            )
    return {"version": version, "tag": tag, "revision": revision, "existing_tag": bool(tags)}


def verify_dist(root, dist, expected):
    if version_at(root) != expected:
        raise ValueError("Checkout version changed during release")
    notes = release_notes(root, expected)
    wheels = list(dist.glob("*.whl"))
    sources = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Release requires exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        reject_ci_checkouts(names)
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        info = Parser().parsestr(archive.read(metadata[0]).decode()) if len(metadata) == 1 else {}
        if info.get("Name") != "amplifier-unified" or info.get("Version") != expected:
            raise ValueError("Wheel metadata does not match release version")
        prefix = "amplifier_web/"
        files = [path for path in (root / "amplifier_web").rglob("*")
                 if path.is_file() and (path.suffix in {".py", ".js"} or path.name == 'release-notes.json' or path.is_relative_to(root / "amplifier_web/static")
                                       or path.is_relative_to(root / "amplifier_web/runtime_deps"))
                 and "__pycache__" not in path.parts and path.suffix != ".pyc"]
        for path in files:
            relative = path.relative_to(root).as_posix()
            if relative not in names or archive.read(relative) != path.read_bytes():
                raise ValueError("Wheel does not contain the validated source/assets: " + relative)
        index = archive.read(prefix + "static/index.html").decode()
        for asset in re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', index):
            if prefix + "static" + asset not in names:
                raise ValueError("Packaged frontend references a missing asset")
    with tarfile.open(sources[0]) as archive:
        names = archive.getnames()
        reject_ci_checkouts(names)
        prefix = names[0].split("/", 1)[0] + "/"
        project = archive.extractfile(prefix + "pyproject.toml").read().decode()
        module = archive.extractfile(prefix + "amplifier_web/__init__.py").read().decode()
        if package_version(project, module) != expected:
            raise ValueError("Source archive does not match release version")
        if notes and archive.extractfile(prefix + 'amplifier_web/release-notes.json').read() != (root / 'amplifier_web/release-notes.json').read_bytes():
            raise ValueError('Source archive does not contain validated release notes')
    (dist / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in [*wheels, *sources]))


def reject_ci_checkouts(names):
    """CI dependencies are test inputs, never application distribution files."""
    for name in names:
        if '.ci' in PurePosixPath(name.replace('\\', '/')).parts:
            raise ValueError('Distribution contains a CI checkout: ' + name)


def release_notes(root, expected):
    path = root / 'amplifier_web/release_notes.py'
    # Rerunning an immutable historical tag must still use its original assets.
    if not path.exists() and tuple(map(int, expected.split('.'))) <= (0, 11, 2):
        return None
    if not path.is_file():
        raise ValueError('Release notes module is required')
    spec = importlib.util.spec_from_file_location('release_history', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = module.parse((root / 'amplifier_web/release-notes.json').read_text(), expected)
    return module.markdown(entries, expected)


def publish(root, repository, tag, revision, dist):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid release repository")
    if tag != "v" + version_at(root) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Release identity does not match validated checkout")
    if run("git", "rev-parse", "HEAD", cwd=root) != revision:
        raise ValueError("Release commit changed after validation")
    verify_dist(root, dist, tag[1:])
    # Recheck remote immediately before mutation. Tags are never moved or deleted.
    remote = run("git", "ls-remote", "origin", "refs/tags/" + tag, "refs/tags/" + tag + "^{}", cwd=root)
    rows = [line.split() for line in remote.splitlines()]
    resolved = next((row[0] for row in rows if row[1].endswith("^{}")), rows[0][0] if rows else None)
    if resolved is not None and resolved != revision:
        raise ValueError("Remote release tag points elsewhere; refusing to move an immutable tag")
    if resolved is None:
        if not run("git", "tag", "--list", tag, cwd=root):
            run("git", "tag", tag, revision, cwd=root)
        elif run("git", "rev-parse", tag + "^{commit}", cwd=root) != revision:
            raise ValueError("Local release tag points elsewhere")
        run("git", "push", "origin", "refs/tags/" + tag, cwd=root)
    releases = json.loads(run("gh", "release", "list", "--repo", repository, "--limit", "1000", "--json", "tagName,isDraft,isPrerelease"))
    release = next((item for item in releases if item["tagName"] == tag), None)
    if release and release.get("isPrerelease"):
        raise ValueError("Existing release is a prerelease; stable publication requires explicit review")
    if release and not release["isDraft"]:
        print("Release already published; immutable tag and assets left unchanged.")
        return
    notes = release_notes(root, tag[1:])
    notes_file = dist / 'RELEASE_NOTES.md'
    if notes:
        notes_file.write_text(notes)
    if release is None:
        run("gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft", "--title",
            "Amplifier Unified " + tag[1:], *(['--notes-file', str(notes_file)] if notes else ['--generate-notes']))
    elif notes:
        run('gh', 'release', 'edit', tag, '--repo', repository, '--notes-file', str(notes_file))
    assets = [*sorted(dist.glob("*.whl")), *sorted(dist.glob("*.tar.gz")), dist / "SHA256SUMS"]
    run("gh", "release", "upload", tag, *map(str, assets), "--repo", repository, "--clobber")
    # Do not make an older repair release latest over an already published newer version.
    newer = any(VERSION.fullmatch(item["tagName"].removeprefix("v"))
                and tuple(map(int, item["tagName"].removeprefix("v").split("."))) > tuple(map(int, tag[1:].split(".")))
                and not item["isDraft"] and not item.get("isPrerelease") for item in releases)
    run("gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest=" + str(not newer).lower())
    print("Published " + tag + " at " + revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "verify", "publish"])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version")
    parser.add_argument("--tag")
    parser.add_argument("--revision")
    parser.add_argument("--repository")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    if args.command == "plan":
        result = plan(root)
        if args.output:
            with args.output.open("a") as output:
                output.write("".join(f"{key}={value}\n" for key, value in result.items()))
        print(json.dumps(result))
    elif args.command == "verify":
        verify_dist(root, args.dist, args.version)
    else:
        publish(root, args.repository, args.tag, args.revision, args.dist)


if __name__ == "__main__":
    main()
