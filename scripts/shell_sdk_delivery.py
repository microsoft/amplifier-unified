"""Select the public shell SDK tarball; builds only verify committed bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import zipfile

REPOSITORY = "bkrabach/amplifier-shell-sdk"
SDK = "packages/shell-sdk"
EXAMPLE = "examples/shell-controls"
RECORD = "components/shell-sdk.source.json"
INSTALLED_RECORD = "amplifier_web/component_delivery/shell-sdk.json"
EXAMPLE_FILES = ("index.js", "manifest.json", "README.md", "package.json", "build.mjs")
PACKAGE_FILES = ("index.js", "index.d.ts", "package.json", "README.md", "LICENSE", "ORIGIN.json",
                 *(EXAMPLE + "/" + name for name in EXAMPLE_FILES))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def regular(root, name):
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Expected a regular selected SDK file: " + name)
    return path.read_bytes()


def inventory(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Expected an owned SDK delivery directory")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Selected SDK delivery cannot contain links")
        if path.is_dir():
            continue
        raw = regular(directory, path.relative_to(directory).as_posix())
        files[path.relative_to(directory).as_posix()] = {"sha256": sha(raw), "bytes": len(raw)}
    return files


def verify(root):
    """Offline, including Git-free source distributions and ordinary installs."""
    try:
        record = json.loads(regular(root, RECORD))
        source, artifact, qualification = record["source"], record["artifact"], record["qualification"]
        version = source["version"]
        filename = f"amplifier-shell-sdk-{version}.tgz"
        url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
        receipt = qualification["receipt"]
        valid = (record["schema"] == 1 and record["kind"] == "amplifier-shell-sdk-artifact"
                 and source["repository"] == REPOSITORY and source["tag"] == "v" + version
                 and re.fullmatch(r"\d+\.\d+\.\d+", version)
                 and all(re.fullmatch(r"[a-f0-9]{40}", source[key]) for key in ("revision", "tree"))
                 and artifact["url"] == url + filename
                 and re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])
                 and type(artifact["bytes"]) is int and artifact["bytes"] > 0
                 and qualification["url"] == url + "qualification.json"
                 and qualification["sha256"] == sha(encoded(receipt))
                 and qualification["bytes"] == len(encoded(receipt))
                 and receipt["package"] == "@amplifier/shell-sdk" and receipt["version"] == version
                 and receipt["tarball"] == filename and receipt["tarballSha256"] == artifact["sha256"]
                 and receipt["strictConsumer"] == "passed")
        files = inventory(root / SDK)
        package = json.loads(regular(root / SDK, "package.json"))
        valid = (valid and files == record["files"] and set(files) == set(PACKAGE_FILES)
                 and sorted((row["path"], row["size"]) for row in receipt["files"])
                 == sorted((name, row["bytes"]) for name, row in files.items())
                 and package["name"] == "@amplifier/shell-sdk" and package["version"] == version
                 and package["type"] == "module" and package["exports"] == "./index.js"
                 and package["types"] == "./index.d.ts" and package["license"] == "MIT"
                 and package["repository"]["url"] == f"https://github.com/{REPOSITORY}.git"
                 and set(package["files"]) == set(PACKAGE_FILES) - {"package.json"}
                 and not any(package.get(key) for key in ("dependencies", "optionalDependencies",
                                                          "peerDependencies", "bundledDependencies"))
                 and inventory(root / EXAMPLE)
                 == {name: files[EXAMPLE + "/" + name] for name in EXAMPLE_FILES})
        if not valid:
            raise ValueError("SDK delivery differs from its selected public artifact")
        return record
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid shell SDK artifact selection") from error


def verify_wheel(root, artifact):
    verify(root)
    expected = {"amplifier_web/bundle_data/" + directory + "/" + name: regular(root / directory, name)
                for directory, names in ((SDK, PACKAGE_FILES), (EXAMPLE, EXAMPLE_FILES)) for name in names}
    expected[INSTALLED_RECORD] = regular(root, RECORD)
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        for directory in (SDK, EXAMPLE):
            prefix = "amplifier_web/bundle_data/" + directory + "/"
            if {name for name in names if name.startswith(prefix)} != {name for name in expected if name.startswith(prefix)}:
                raise ValueError("Wheel SDK membership differs from selection")
        if any(names.count(name) != 1 or archive.read(name) != raw for name, raw in expected.items()):
            raise ValueError("Wheel SDK bytes differ from selection")


def select(root, dist, revision, tree, archive_sha256, qualification_sha256):
    """Explicit maintainer selection. No network, Node, package execution or build."""
    raw_qualification = regular(dist, "qualification.json")
    if sha(raw_qualification) != qualification_sha256:
        raise ValueError("Published SDK qualification checksum changed")
    receipt = json.loads(raw_qualification)
    version = receipt["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a release SDK version")
    filename = f"amplifier-shell-sdk-{version}.tgz"
    if {path.name for path in dist.iterdir()} != {filename, "SHA256SUMS", "qualification.json"}:
        raise ValueError("Unexpected SDK release assets")
    raw_archive = regular(dist, filename)
    if sha(raw_archive) != archive_sha256 or regular(dist, "SHA256SUMS") != f"{archive_sha256}  {filename}\n".encode():
        raise ValueError("Published SDK tarball checksum changed")
    files = {}
    with tarfile.open(dist / filename) as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("package/")
            if member.name != "package/" + name or name not in PACKAGE_FILES or name in files or not member.isfile():
                raise ValueError("Unexpected SDK tarball member")
            files[name] = archive.extractfile(member).read()
    if set(files) != set(PACKAGE_FILES):
        raise ValueError("Incomplete SDK tarball")
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
    record = {"schema": 1, "kind": "amplifier-shell-sdk-artifact",
              "source": {"repository": REPOSITORY, "revision": revision, "tree": tree,
                         "tag": "v" + version, "version": version},
              "artifact": {"url": url + filename, "sha256": archive_sha256, "bytes": len(raw_archive)},
              "qualification": {"url": url + "qualification.json", "sha256": qualification_sha256,
                                "bytes": len(raw_qualification), "receipt": receipt},
              "files": {name: {"sha256": sha(raw), "bytes": len(raw)} for name, raw in sorted(files.items())}}
    record_path = root / RECORD
    old_record = regular(root, RECORD) if record_path.exists() or record_path.is_symlink() else None
    if old_record is not None:
        verify(root)
    elif any((root / name).exists() or (root / name).is_symlink() for name in (SDK, EXAMPLE)):
        raise ValueError("Preserve unselected SDK source; first retire its authored copies explicitly")
    for directory in (SDK, EXAMPLE):
        if (root / directory).parent.is_symlink():
            raise ValueError("SDK delivery parent cannot be a link")
    temporary = Path(tempfile.mkdtemp(prefix=".shell-sdk-selection-", dir=root / "components"))
    retain = False
    try:
        ready = temporary / "ready"
        for name, raw in files.items():
            path = ready / SDK / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        for name in EXAMPLE_FILES:
            path = ready / EXAMPLE / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[EXAMPLE + "/" + name])
        (ready / RECORD).parent.mkdir(parents=True)
        (ready / RECORD).write_bytes(encoded(record))
        verify(ready)
        previous = temporary / "previous"
        previous.mkdir()
        if old_record is not None:
            (previous / "record.json").write_bytes(old_record)
        installed = []
        moved = []
        try:
            for index, directory in enumerate((SDK, EXAMPLE)):
                destination = root / directory
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    destination.rename(previous / str(index))
                    moved.append((index, destination))
                (ready / directory).rename(destination)
                installed.append(destination)
            (ready / RECORD).replace(record_path)
        except BaseException:
            retain = True
            try:
                for index, destination in enumerate(reversed(installed)):
                    destination.rename(temporary / ("failed-" + str(index)))
                for index, destination in reversed(moved):
                    (previous / str(index)).rename(destination)
                if old_record is not None:
                    record_path.write_bytes(old_record)
                elif record_path.exists():
                    record_path.unlink()
            except OSError as error:
                raise ValueError(f"Restore prior SDK selection from {temporary}") from error
            retain = False
            raise
    finally:
        if not retain:
            shutil.rmtree(temporary)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("select", "verify"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--tree")
    parser.add_argument("--archive-sha256")
    parser.add_argument("--qualification-sha256")
    args = parser.parse_args()
    if args.action == "select":
        if not all((args.dist, args.revision, args.tree, args.archive_sha256, args.qualification_sha256)):
            parser.error("select requires original assets, revision, tree and both artifact checksums")
        record = select(args.root, args.dist, args.revision, args.tree,
                        args.archive_sha256, args.qualification_sha256)
    else:
        record = verify(args.root)
    print(json.dumps({"selected": record["source"], "verified": True}))


if __name__ == "__main__":
    main()
