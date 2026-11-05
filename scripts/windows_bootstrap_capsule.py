"""Package an already-built Windows wheelhouse as an explicit prototype capsule.

This standard-library tool does not resolve/install dependencies or claim Windows
support. Supply the exact candidate wheelhouse built by the release owner.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import zipfile


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_identity(path: Path) -> tuple[str, str]:
    tags = path.name.removesuffix(".whl").rsplit("-", 3)
    if len(tags) != 4 or not any(
        compatible_tag(interpreter, abi, platform)
        for interpreter in tags[1].split(".")
        for abi in tags[2].split(".")
        for platform in tags[3].split(".")
    ):
        raise ValueError("Wheelhouse must target ordinary Windows x64 or pure Python.")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(names) != 1:
            raise ValueError("Wheel must contain one distribution metadata identity.")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    name, version = str(metadata["Name"]), str(metadata["Version"])
    if not re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", name):
        raise ValueError("Invalid wheel distribution name.")
    if not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version):
        raise ValueError("Invalid wheel version.")
    return re.sub(r"[-_.]+", "-", name).lower(), version


def compatible_tag(interpreter: str, abi: str, platform: str) -> bool:
    """Conservative ordinary CPython 3.13 x64 tag subset; never free-threaded."""
    if platform not in ("any", "win_amd64"):
        return False
    if abi == "none":
        return interpreter in {"py3", "cp313", *(f"py3{minor}" for minor in range(14))}
    if platform != "win_amd64":
        return False
    if interpreter == "cp313" and abi == "cp313":
        return True
    match = re.fullmatch(r"cp3(\d+)", interpreter)
    return abi == "abi3" and match is not None and 2 <= int(match[1]) <= 13


def checked_inventory(path: Path, source: str, identities: dict) -> bytes:
    """Check an independently reviewed inventory, not one inferred from inputs.

    The owner establishes METADATA closure and source provenance first. Equality
    prevents adding dev wheels or omitting a reviewed dependency; it does not
    independently establish those prior facts.
    """
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("Unsafe reviewed inventory input.")
    raw = path.read_bytes()
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("schema") != 1 or record.get("source") != source
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("lockSha256", "")))
            or not isinstance(record.get("packages"), dict) or not record["packages"]):
        raise ValueError("Supply the reviewed source/lock inventory.")
    for name, item in record["packages"].items():
        if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or not isinstance(item, dict) or set(item) != {"version", "sha256"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))):
            raise ValueError("Malformed reviewed package inventory.")
    if record["packages"] != identities:
        raise ValueError("Wheelhouse differs from the reviewed production inventory.")
    return raw


def build(wheelhouse: Path, output: Path, *, inventory: Path, source: str, version: str, python: str, url: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Supply exact source commit and app version.")
    if not re.fullmatch(r"3\.13\.\d+", python):
        raise ValueError("Supply an ordinary CPython 3.13 patch supported by the pinned uv.")
    if (not re.fullmatch(r"https://github\.com/microsoft/amplifier-unified/releases/download/[^/?#]+/[^/?#]+\.zip", url)
            or "/download/latest/" in url or "/download/main/" in url):
        raise ValueError("Supply the intended immutable official capsule asset URL.")
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels or len(wheels) > 256:
        raise ValueError("Supply a complete bounded Windows wheelhouse.")
    identities = {}
    requirements = []
    for wheel in wheels:
        if wheel.is_symlink() or not wheel.is_file() or wheel.stat().st_size > 134217728:
            raise ValueError("Unsafe wheel input.")
        name, item_version = wheel_identity(wheel)
        if name in identities:
            raise ValueError("Duplicate distribution in wheelhouse.")
        digest = sha256(wheel)
        identities[name] = {"version": item_version, "sha256": digest}
        requirements.append(f"{name}=={item_version} --hash=sha256:{digest}")
    if sum(wheel.stat().st_size for wheel in wheels) > 400 * 1024 * 1024:
        raise ValueError("Expanded capsule wheel budget exceeded.")
    if identities.get("amplifier-unified", {}).get("version") != version:
        raise ValueError("Wheelhouse does not contain the selected app version.")
    reviewed = checked_inventory(inventory, source, identities)
    # No sdist, VCS resolution, editable install or native build on user machines.
    # pip check does not establish selected extras, exact source or set equality.
    launch = Path(__file__).with_name("windows_prototype_launch.py")
    contents = {
        "requirements.txt": ("\n".join(sorted(requirements)) + "\n").encode(),
        "launch.py": launch.read_bytes(),
        "capsule.json": json.dumps({"source": source, "version": version}, sort_keys=True).encode(),
        "inventory.json": reviewed,
    }
    output.mkdir(parents=True, exist_ok=False)
    capsule = output / "windows-prototype.zip"
    with zipfile.ZipFile(capsule, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        for wheel in wheels:
            info = zipfile.ZipInfo("wheels/" + wheel.name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, wheel.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    if capsule.stat().st_size > 134217728:
        raise ValueError("Capsule exceeds bootstrap download budget; retain failed output for review.")
    record = {
        "schemaVersion": 1, "status": "prototype", "source": source, "version": version,
        "architecture": "x86_64", "python": python,
        "capsule": {"url": url, "sha256": sha256(capsule)},
    }
    manifest = output / "windows-prototype.json"
    manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"manifestSha256": sha256(manifest), "capsuleSha256": record["capsule"]["sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True,
                        help="Independently reviewed production closure/source/lock and actual wheel hashes")
    parser.add_argument("--output", type=Path, required=True, help="New, absent directory; never replace previous artifacts")
    for flag in ("source", "version", "python", "url"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.wheelhouse, args.output, inventory=args.inventory, source=args.source, version=args.version,
                           python=args.python, url=args.url), sort_keys=True))


if __name__ == "__main__":
    main()