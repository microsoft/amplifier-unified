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
        # Atomic create must never adopt/overwrite a key that already exists,
        # including a collision that appeared after the install preflight.
        try:
            shell.registry(root, shell.registration_values(root, __version__))
        except ValueError:
            report["existingRegistryCreateRefused"] = True
        else:
            raise AssertionError("Registry create must refuse an existing registration.")
        if shell.registry(root) != registration:
            raise AssertionError("Registry collision overwrote existing values.")
        # Preparing removal retains the entire file set. Actual NTFS sharing
        # must block same-account link writes/replacement before any effect.
        with shell.removal(root):
            import winreg
            key = shell.REGISTRY + shell.identity(root)
            last = list(links)[-1]
            for operation in ("write-link", "replace-link"):
                try:
                    if operation == "write-link":
                        with (folder / last).open("wb") as changed:
                            changed.write(b"must never write")
                    else:
                        write_private(folder / last, b"must never replace", owned_root=folder)
                except OSError:
                    report[operation + "Refused"] = True
                else:
                    raise AssertionError("Prepared removal allowed concurrent mutation: " + operation)
        if shell.registry(root) != registration or any(not (folder / item).exists() for item in links):
            raise AssertionError("Abandoned preparation changed app entries.")
        report["abandonedPreparationUnchanged"] = True
        # Nontransactional registry writers can win, aborting our transaction.
        # The commit must refuse BEFORE links or a Scheduler task are touched.
        for operation in ("change-key", "replace-key"):
            interfered = False
            try:
                with shell.removal(root) as finish:
                    if operation == "change-key":
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as changed:
                            winreg.SetValueEx(changed, "DisplayVersion", 0, winreg.REG_SZ, "fixture-racer")
                    else:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as changed:
                            for field, (value, kind) in registration.items():
                                winreg.SetValueEx(changed, field, 0, kind, value)
                            winreg.SetValueEx(changed, "DisplayVersion", 0, winreg.REG_SZ, "fixture-racer")
                    interfered = True
                    finish()
            except OSError:
                pass
            else:
                raise AssertionError("Conflicting registry removal must refuse.")
            observed = shell.registry(root)
            if any(not (folder / item).exists() for item in links) or observed is None:
                raise AssertionError("A registry conflict removed app entries.")
            if interfered:
                expected = dict(registration)
                expected["DisplayVersion"] = ("fixture-racer", winreg.REG_SZ)
                if observed != expected:
                    raise AssertionError("Conflicting registration was overwritten/deleted.")
                # Restore only this exact fixture-authored negative-control value.
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as changed:
                    winreg.SetValueEx(changed, "DisplayVersion", 0, *reversed(registration["DisplayVersion"]))
            elif observed != registration:
                raise AssertionError("A refused registry race changed the registration.")
            report[operation + "ConflictSafe"] = True
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
