"""Actual user-shell COM/HKCU/remove checks in an installed private candidate.

No task/server/browser/model action. Not clean-Windows installer or GUI proof.
Files and app-owned registry/shortcut evidence remain inspectable on failure;
never delete a changed/partial object simply to make cleanup appear successful.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def run(root):
    from amplifier_web import windows_consumer_shell as shell
    from amplifier_web.windows_files import directory
    from amplifier_web.deployment import write_private
    from amplifier_web import __version__
    if os.name != "nt" or Path(sys.prefix) != root / "tools/amplifier-unified":
        raise ValueError("Use only this installed native fixture candidate.")
    output = root / "native-shell-v1.json"
    if output.exists() or (root / shell.FILE).exists():
        raise ValueError("Inspect the original shell operation before any retry.")
    report = {"schema": 1, "status": "RUNNING", "publicAcceptance": False,
              "cleanWindowsAcceptance": False, "browserOrServerStarted": False,
              "phase": "install-admitting", "sourceHashes": {}}
    for module in (shell,):
        path = Path(module.__file__)
        report["sourceHashes"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    def save():
        write_private(output, json.dumps(report, indent=2), owned_root=root)
    save()
    try:
        report["install"] = shell.install(root, __version__)
        report["phase"] = "installed"
        save()
        folder = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
        python, links = shell.entries(root)
        observed = {}
        for name, args in links.items():
            with shell.shell_link() as link:
                target, arguments = link.read(folder / name)
            if target != str(python) or arguments != args:
                raise AssertionError("Actual ShellLink readback differs from installed binding.")
            observed[name] = {"target": target, "arguments": arguments}
        report["links"] = observed
        registration = shell.registry(root)
        if not registration or registration["InstallLocation"][0] != str(root):
            raise AssertionError("Actual HKCU installed-app entry is missing or foreign.")
        report["registryFields"] = sorted(registration)
        name = next(iter(links))
        with directory(folder, owned_root=folder) as owned:
            original = owned.read_private(name, limit=65536)
        report["phase"] = "changed-link-negative"
        save()
        # This is an exact fixture-owned shortcut, not installed application
        # source or a user-created link. The production remover must refuse it.
        write_private(folder / name, b"controlled changed shortcut", owned_root=folder)
        try:
            shell.remove(root)
        except ValueError:
            report["changedLinkRemovalRefused"] = True
        else:
            raise AssertionError("Changed shortcut must not be removed.")
        if shell.registry(root) != registration or any(not (folder / item).exists() for item in links):
            raise AssertionError("Negative control removed app entries.")
        write_private(folder / name, original, owned_root=folder)
        report["phase"] = "normal-remove-admitting"
        save()
        report["remove"] = shell.remove(root)
        if shell.registry(root) is not None or any((folder / item).exists() for item in links):
            raise AssertionError("Normal removal did not remove all exact app entries.")
        report.update(status="PASS", phase="removed", registryAbsent=True,
                      shortcutsAbsent=True, emptyPrivateFolderRetained=folder.is_dir(),
                      installationFilesRetained=(root / "tools/amplifier-unified").is_dir())
    except BaseException as error:
        report.update(status="FAIL", errorType=type(error).__name__)
        raise
    finally:
        save()
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    run(parser.parse_args().root)
