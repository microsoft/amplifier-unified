"""Select published scheduling bytes; verify their embedded delivery offline."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import tomllib
import zipfile

DELIVERY = "components/scheduling-delivery"
RECORD = "components/scheduling.source.json"
PACKAGE = "amplifier_scheduling"
INSTALLED_RECORD = "amplifier_web/component_delivery/scheduling.json"
INSTALLED_LICENSE = "amplifier_web/component_delivery/scheduling-LICENSE"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(root, name):
    parts = PurePosixPath(name).parts
    path = root / name
    if (not parts or name.startswith("/") or "\\" in name or
            any(part in {".", ".."} for part in parts) or
            not path.resolve().is_relative_to(root.resolve()) or
            path.is_symlink() or not path.is_file()):
        raise ValueError("Expected a regular selected file: " + name)
    return path.read_bytes()


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Selected delivery cannot contain links")
        if path.is_dir() or (path.parent.name == "__pycache__" and path.suffix == ".pyc"):
            continue
        name = path.relative_to(root).as_posix()
        result[name] = sha(regular(root, name))
    return result


def requirement_key(value):
    # Keep the published marker; quote/spacing differences are metadata formatting.
    return re.sub(r"\s+", "", value).replace('"', "'")


def verify(root):
    """The authoring tree is deliberately not an input to app delivery."""
    record = json.loads(regular(root, RECORD))
    source = record["source"]
    version = source["version"]
    wheel = f"amplifier_scheduling-{version}-py3-none-any.whl"
    expected_url = (f"https://github.com/{source['repository']}/releases/download/"
                    f"{source['tag']}/{wheel}")
    if (record.get("schema") != 1 or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]) or
            not re.fullmatch(r"[A-Za-z0-9_.+-]+", source["tag"]) or
            not re.fullmatch(r"[a-f0-9]{40}", source["revision"]) or
            not re.fullmatch(r"\d+\.\d+\.\d+", version) or
            record["artifact"]["url"] != expected_url or
            not re.fullmatch(r"[a-f0-9]{64}", record["artifact"]["sha256"]) or
            {key: record["artifact"][key] for key in ("sha256", "bytes")} != source["artifacts"][wheel] or
            sha((json.dumps(source, indent=2) + "\n").encode()) != record["sourceReceiptSha256"]):
        raise ValueError("Invalid scheduling artifact selection")
    files = record["files"]
    if (not isinstance(files, dict) or "LICENSE" not in files or
            PACKAGE + "/__init__.py" not in files or
            files != {name: digest for name, digest in source["sourceFiles"].items()
                      if name == "LICENSE" or name.startswith(PACKAGE + "/")} or
            inventory(root / DELIVERY) != files):
        raise ValueError("Scheduling delivery differs from its selected artifact")
    project = tomllib.loads((root / "pyproject.toml").read_text())
    packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    if [path for path in packages if path.rstrip("/").endswith(PACKAGE)] != [DELIVERY + "/" + PACKAGE]:
        raise ValueError("Unified must package only the selected scheduling owner")
    declared = {requirement_key(value) for value in project["project"]["dependencies"]}
    if not {requirement_key(value) for value in record["runtimeRequirements"]} <= declared:
        raise ValueError("Unified omits a selected scheduling runtime requirement")
    return record


def verify_wheel(root, artifact, *, editable=False):
    record = verify(root)
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or not metadata[0].startswith("amplifier_unified-"):
            raise ValueError("Scheduling must not introduce another distribution owner")
        if editable:
            return  # The editable path mapping is tested by its installed import.
        expected = {name: regular(root / DELIVERY, name) for name in record["files"] if name != "LICENSE"}
        if {name for name in names if name.startswith(PACKAGE + "/")} != set(expected):
            raise ValueError("Wheel scheduling membership differs from selection")
        expected.update({INSTALLED_RECORD: regular(root, RECORD),
                         INSTALLED_LICENSE: regular(root / DELIVERY, "LICENSE")})
        if any(names.count(name) != 1 or archive.read(name) != raw for name, raw in expected.items()):
            raise ValueError("Wheel scheduling bytes differ from selection")


def select(root, dist, revision, receipt_sha256):
    """Explicit maintainer operation. Builds never fetch, select, or refresh."""
    receipt_bytes = regular(dist, "scheduling-source.json")
    if sha(receipt_bytes) != receipt_sha256:
        raise ValueError("Published source receipt checksum changed")
    spec = importlib.util.spec_from_file_location("scheduling_release", Path(__file__).with_name("scheduling_release.py"))
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    receipt = json.loads(receipt_bytes)
    if receipt["revision"] != revision:
        raise ValueError("Source receipt differs from the requested published revision")
    wheel, _ = publisher.artifact_names(receipt)
    names = (*publisher.artifact_names(receipt), publisher.RECEIPT)
    if set(path.name for path in dist.iterdir()) != {*names, "SHA256SUMS"}:
        raise ValueError("Unexpected scheduling release asset set")
    if publisher.package_identity(dist, receipt) != receipt["artifacts"]:
        raise ValueError("Published scheduling artifact checksums changed")
    if (dist / "SHA256SUMS").read_text() != "".join(f"{sha(regular(dist, name))}  {name}\n" for name in names):
        raise ValueError("Published scheduling checksums differ")
    with zipfile.ZipFile(dist / wheel) as archive:
        info = f"amplifier_scheduling-{receipt['version']}.dist-info/"
        metadata = BytesParser().parsebytes(archive.read(info + "METADATA"))
        requirements = metadata.get_all("Requires-Dist", [])
        # Scheduling's optional test runner is not an app runtime requirement.
        runtime = [value for value in requirements if not value.endswith('; extra == "test"')]
        if any("extra" in value for value in runtime):
            raise ValueError("Review new scheduling extras before selecting delivery")
        files = {name: archive.read(name) for name in receipt["sourceFiles"] if name.startswith(PACKAGE + "/")}
        files["LICENSE"] = archive.read(info + "licenses/LICENSE")
    if sha(files["LICENSE"]) != receipt["sourceFiles"]["LICENSE"]:
        raise ValueError("Wheel license differs from selected source")
    record = {"schema": 1, "source": receipt,
              "artifact": {"url": f"https://github.com/{receipt['repository']}/releases/download/{receipt['tag']}/{wheel}",
                           **receipt["artifacts"][wheel]},
              "sourceReceiptSha256": receipt_sha256, "runtimeRequirements": runtime,
              "files": {name: sha(raw) for name, raw in files.items()}}
    destination = root / DELIVERY
    temporary = Path(tempfile.mkdtemp(prefix=".scheduling-selection-", dir=root / "components"))
    retain = False
    try:
        ready = temporary / "ready"; ready.mkdir()
        for name, raw in files.items():
            path = ready / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        previous = temporary / "previous"
        record_path = root / RECORD
        old_record = record_path.read_bytes() if record_path.exists() else None
        if old_record is not None:
            (temporary / "previous-record.json").write_bytes(old_record)
        next_record = temporary / "record.json"
        next_record.write_text(json.dumps(record, indent=2) + "\n")
        if destination.exists():
            # New dependencies may differ; only require the old snapshot itself clean.
            if old_record is None or inventory(destination) != json.loads(old_record)["files"]:
                raise ValueError("Preserve locally modified scheduling delivery")
        try:
            if destination.exists():
                destination.rename(previous)
            ready.rename(destination)
            next_record.replace(record_path)
        except BaseException:
            try:
                if not ready.exists() and destination.exists():
                    destination.rename(temporary / "failed")
                if previous.exists():
                    previous.rename(destination)
                if old_record is not None:
                    record_path.write_bytes(old_record)
                elif record_path.exists():
                    record_path.unlink()
            except OSError as error:
                retain = True
                raise ValueError(f"Restore the prior scheduling selection from {temporary}") from error
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
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args()
    if args.action == "select":
        if not all((args.dist, args.revision, args.receipt_sha256)):
            parser.error("select requires --dist, --revision and --receipt-sha256")
        select(args.root, args.dist, args.revision, args.receipt_sha256)
    else:
        verify(args.root)


if __name__ == "__main__":
    main()
