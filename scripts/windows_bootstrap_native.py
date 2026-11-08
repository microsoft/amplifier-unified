"""Prepare reviewable sources for the prototype Windows x64 native bootstrap.

Run on a build machine, never a prerequisite on the target PC. Compilation uses
Windows' .NET Framework compiler; the output uses only the in-box .NET Framework.
This does not publish, sign, qualify or execute an installer. The existing C#
InstallStorage body remains canonical in install.ps1 and is reused at BUILD time,
not by executing a policy-refused PowerShell script on the installation target.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sources(script_dir: Path) -> dict[str, bytes]:
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
    quote = json.dumps
    pins = [
        "using System.Collections.Generic;",
        "namespace UnifiedBootstrap { internal static class NativePins {",
        f"internal const string Source={quote(module.APP_SOURCE)};",
        f"internal const string Version={quote(module.APP_VERSION)};",
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


def prepare(script_dir: Path, destination: Path) -> dict:
    contents = sources(script_dir)
    if destination.exists():
        raise ValueError("Native build output must be absent; preserve previous artifacts.")
    destination.mkdir(parents=True)
    for name, content in contents.items():
        (destination / name).write_bytes(content)
    report = {
        "schema": 1,
        "kind": "prototype-native-bootstrap-build-inputs",
        "architecture": "x64",
        "targetRuntime": "in-box .NET Framework 4.8, Windows11",
        "powershellExecutionOnTarget": False,
        "sources": {name: {"bytes": len(content), "sha256": _sha(content)} for name, content in contents.items()},
        "references": ["System.Management.dll", "System.Web.Extensions.dll", "System.IO.Compression.dll",
                       "System.IO.Compression.FileSystem.dll", "Microsoft.CSharp.dll"],
        "entry": "UnifiedBootstrap.NativeInstaller",
        "compilerOptions": ["/nologo", "/target:exe", "/platform:x64", "/optimize+"],
        "compiled": False,
        "signed": False,
        "published": False,
    }
    (destination / "build-inputs.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parent, args.output), indent=2))


if __name__ == "__main__":
    main()
