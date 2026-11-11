"""Prepare reviewable sources for the prototype Windows x64 native bootstrap.

Run on a build machine, never a prerequisite on the target PC. Compilation uses
Windows' .NET Framework compiler; the output uses only the in-box .NET Framework.
This does not publish, sign, qualify or execute an installer. The existing C#
InstallStorage body remains canonical in install.ps1 and is reused at BUILD time,
not by executing a policy-refused PowerShell script on the installation target.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def consumer_plan(value):
    """Closed build input from the selected installed Windows graph.

    This validates structure and bindings, not independent publication/closure.
    The release owner supplies already-qualified original artifact and graph.
    """
    if (not isinstance(value, dict) or set(value) != {
            "schema", "kind", "source", "version", "python", "artifact", "graph"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["kind"] != "windows-consumer-preview"
            or not isinstance(value["source"], str) or not re.fullmatch("[a-f0-9]{40}", value["source"])
            or not isinstance(value["version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", value["version"])
            or not isinstance(value["python"], str) or not re.fullmatch(r"3\.13\.\d+", value["python"])):
        raise ValueError("Supply the exact qualified Windows consumer build plan.")
    version, source = value["version"], value["source"]
    artifact = value["artifact"]
    url = f"https://github.com/microsoft/amplifier-unified/releases/download/v{version}/amplifier_unified-{version}-py3-none-any.whl"
    if (not isinstance(artifact, dict) or set(artifact) != {"url", "sha256", "bytes"}
            or artifact["url"] != url or not isinstance(artifact["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", artifact["sha256"])
            or type(artifact["bytes"]) is not int or not 0 < artifact["bytes"] <= 134217728):
        raise ValueError("The consumer plan requires the exact official app wheel.")
    graph = value["graph"]
    if not isinstance(graph, list) or not 1 <= len(graph) <= 256:
        raise ValueError("The consumer plan requires a bounded qualified graph.")
    names = []
    for row in graph:
        if (not isinstance(row, dict) or set(row) - {"name", "version", "url", "revision", "subdirectory", "delivery"}
                or not isinstance(row.get("name"), str) or not re.fullmatch("[a-z0-9][a-z0-9-]*", row["name"])
                or not isinstance(row.get("version"), str) or not re.fullmatch("[A-Za-z0-9.!+_-]+", row["version"])):
            raise ValueError("The consumer graph is not canonical.")
        names.append(row["name"])
        if "url" in row:
            if not isinstance(row["url"], str):
                raise ValueError("The consumer graph has an invalid source URL.")
            remote = urlsplit(row["url"])
            if (remote.scheme != "https" or remote.hostname != "github.com" or remote.username
                    or remote.password or remote.query or remote.fragment or remote.port not in {None, 443}
                    or not re.fullmatch(r"/microsoft/amplifier[-a-z0-9]*(?:\.git)?", remote.path)
                    or not isinstance(row.get("revision"), str) or not re.fullmatch("[a-f0-9]{40}", row["revision"])):
                raise ValueError("The consumer graph has an unqualified/custom source.")
        elif "revision" in row or "subdirectory" in row:
            raise ValueError("The consumer graph has an incomplete source.")
        subdirectory = row.get("subdirectory")
        if subdirectory is not None and (not isinstance(subdirectory, str)
                or not re.fullmatch("[A-Za-z0-9_/-]+", subdirectory)
                or any(part in {"", ".", ".."} for part in subdirectory.split("/"))):
            raise ValueError("The consumer graph has an unsafe source subdirectory.")
        if "delivery" in row and row["name"] != "amplifier-unified":
            raise ValueError("Only the selected app can have release delivery metadata.")
    app = {"name": "amplifier-unified", "version": version,
           "url": "https://github.com/microsoft/amplifier-unified", "revision": source,
           "delivery": {key: artifact[key] for key in ("url", "sha256")}}
    if names != sorted(set(names)) or [row for row in graph if row["name"] == "amplifier-unified"] != [app]:
        raise ValueError("The consumer graph and selected wheel disagree.")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > 256 * 1024:
        raise ValueError("Consumer build plan exceeds its budget.")
    return raw


def sources(script_dir: Path, *, consumer=None) -> dict[str, bytes]:
    installer = (script_dir / "install.ps1").read_text(encoding="utf-8")
    matches = re.findall(r"Add-Type -TypeDefinition @'\n(.*?)\n'@", installer, re.S)
    if len(matches) != 1 or "public sealed class InstallStorage" not in matches[0]:
        raise ValueError("Expected the one canonical bootstrap InstallStorage source.")
    helper = installer.split("function Install-VerifiedHelper", 1)[1].split("$helper = $helpers[$Name]", 1)[0]
    def field(name: str) -> list[str]:
        found = re.findall(rf"^\s*{re.escape(name)} = '([^']+)'$", helper, re.M)
        if len(found) != 2:
            raise ValueError(f"Expected exactly two canonical helper {name} entries.")
        return found
    urls, hashes, versions = field("url"), field("sha256"), field("version")
    licenses = dict(re.findall(r"'(LICENSE-(?:MIT|APACHE))' = '([0-9a-f]{64})'", helper))
    license_source = re.findall(r"licenseSource = '([^']+)'", helper)
    if set(licenses) != {"LICENSE-MIT", "LICENSE-APACHE"} or len(license_source) != 1:
        raise ValueError("Canonical uv license inputs are incomplete.")
    for url in urls:
        if not url.startswith("https://github.com/"):
            raise ValueError("Bootstrap helper is not an exact official GitHub asset.")
    if not all(re.fullmatch("[0-9a-f]{64}", value) for value in hashes):
        raise ValueError("Canonical helper digests are malformed.")
    spec = importlib.util.spec_from_file_location("capsule_build_identity", script_dir / "windows_bootstrap_capsule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plan = consumer_plan(consumer) if consumer is not None else b""
    quote = json.dumps
    pins = [
        "using System.Collections.Generic;",
        "namespace UnifiedBootstrap { internal static class NativePins {",
        f"internal const string Source={quote(module.APP_SOURCE)};",
        f"internal const string Version={quote(module.APP_VERSION)};",
        f"internal const string ConsumerPlan={quote(base64.b64encode(plan).decode())};",
        f"internal const string UvUrl={quote(urls[0])};",
        f"internal const string UvHash={quote(hashes[0])};",
        f"internal const string UvVersion={quote(versions[0])};",
        f"internal const string GitUrl={quote(urls[1])};",
        f"internal const string GitHash={quote(hashes[1])};",
        f"internal const string UvLicenseSource={quote(license_source[0])};",
        "internal static readonly Dictionary<string,string> UvLicenses=new Dictionary<string,string> {",
        ",\n".join("{" + quote(name) + "," + quote(digest) + "}" for name, digest in sorted(licenses.items())),
        "}; } }",
    ]
    return {
        "InstallStorage.cs": (matches[0] + "\n").encode(),
        "NativePins.cs": ("\n".join(pins) + "\n").encode(),
        "NativeInstaller.cs": (script_dir / "windows_bootstrap_native.cs").read_bytes(),
    }


def prepare(script_dir: Path, destination: Path, *, consumer=None) -> dict:
    contents = sources(script_dir, consumer=consumer)
    if destination.exists():
        raise ValueError("Native build output must be absent; preserve previous artifacts.")
    destination.mkdir(parents=True)
    for name, content in contents.items():
        (destination / name).write_bytes(content)
    report = {
        "schema": 1,
        "kind": ("consumer-native-bootstrap-build-inputs" if consumer is not None
                 else "prototype-native-bootstrap-build-inputs"),
        "architecture": "x64",
        "targetRuntime": "in-box .NET Framework 4.8, Windows11",
        "powershellExecutionOnTarget": False,
        "sources": {name: {"bytes": len(content), "sha256": _sha(content)} for name, content in contents.items()},
        "references": ["System.Management.dll", "System.Web.Extensions.dll", "System.IO.Compression.dll",
                       "System.IO.Compression.FileSystem.dll", "Microsoft.CSharp.dll",
                       "System.Windows.Forms.dll", "System.Drawing.dll"],
        "entry": "UnifiedBootstrap.NativeInstaller",
        "compilerOptions": ["/nologo", "/target:" + ("winexe" if consumer is not None else "exe"),
                            "/platform:x64", "/optimize+"],
        "compiled": False,
        "signed": False,
        "published": False,
    }
    if consumer is not None:
        report.update(consumerPlanSha256=_sha(consumer_plan(consumer)),
                      source=consumer["source"], version=consumer["version"],
                      consumerNativeAcceptance=False, signingDeferred=True)
    (destination / "build-inputs.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--consumer-plan", type=Path,
                        help="Exact already-qualified Windows release/graph; embeds no-argument preview setup")
    args = parser.parse_args()
    consumer = None
    if args.consumer_plan is not None:
        if args.consumer_plan.is_symlink() or args.consumer_plan.stat().st_size > 256 * 1024:
            raise ValueError("Unsafe or oversized consumer build input.")
        consumer = json.loads(args.consumer_plan.read_bytes())
    print(json.dumps(prepare(Path(__file__).resolve().parent, args.output, consumer=consumer), indent=2))


if __name__ == "__main__":
    main()
