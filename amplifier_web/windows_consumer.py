"""Installed consumer entry; native setup owns acquisition, not this shortcut.

The entry only composes the existing installer/service/browser boundaries. It
never downloads on reopen, copies accounts or force-stops a host. Its one trust
step asks Windows, with the person's own confirmation, to trust this
installation's key-less local certificate; it never changes trust silently.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

RECEIPT = "consumer-install.json"
QUALIFICATION = "consumer-qualified.json"
RECORD = "consumer-maintenance.json"


def _receipt_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def installation(root):
    from .windows_files import address, directory
    from .app_release_artifacts import validate_descriptor, source_revision
    root = address(root)
    with directory(root, owned_root=root) as owned:
        value = json.loads(owned.read_private(RECEIPT, limit=256 * 1024))
    if (not isinstance(value, dict) or set(value) != {
            "schema", "kind", "version", "revision", "artifact", "graph", "uv", "git"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["kind"] != "windows-consumer-preview"):
        raise ValueError("The consumer installation receipt is invalid.")
    artifact = validate_descriptor(value["artifact"], value["version"])
    if Path(artifact["path"]) != root / Path(artifact["path"]).name:
        raise ValueError("The installation artifact belongs to another root.")
    prefix = root / "tools/amplifier-unified"
    dist = metadata.distribution("amplifier-unified")
    if (Path(sys.prefix) != prefix or Path(dist.locate_file("amplifier_web")) != Path(__file__).parent
            or dist.version != value["version"] or source_revision(dist) != value["revision"]):
        raise ValueError("Use this consumer installation's original shortcut.")
    expected = {"uv": root / "uv/uv.exe", "git": root / "git/cmd/git.exe"}
    from .windows_read_files import open_file
    for name, path in expected.items():
        if value[name] != str(path):
            raise ValueError("The private helper binding changed.")
        with directory(path.parent, owned_root=root), open_file(path) as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("The installation's private helper is missing.")
    return root, value


def audit(root):
    """Setup-only full audit; an ordinary shortcut does not reinstall or resolve."""
    from .app_release_artifacts import verify
    from .manual_app_handoff import _payload
    from .app_component_graph import GRAPH_PROBE, normalized, validate_graph
    root, value = installation(root)
    artifact = verify(value["artifact"], value["version"], value["revision"])
    # Existing native payload verifier owns RECORD/archive/member checks.
    _payload({"artifact": artifact, "binding": {
        "version": value["version"], "revision": value["revision"]}})
    result = subprocess.run([sys.executable, "-I", "-c", GRAPH_PROBE],
                            capture_output=True, text=True, timeout=30, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    graph = normalized(json.loads(result.stdout), artifact=artifact) if result.returncode == 0 else None
    if graph != validate_graph(value["graph"]):
        raise ValueError("The installed application graph differs from setup's qualified selection.")
    from .application_generations import bootstrap_identity
    from .app_component_graph import digest
    from .app_updates import installed_extras
    from .deployment import write_private
    proof = {"schema": 1, "receiptDigest": _receipt_digest(value),
             "installation": bootstrap_identity(sys.prefix),
             "qualification": {
                 "app": next(row for row in graph if row["name"] == "amplifier-unified"),
                 "artifact": artifact, "extras": installed_extras(),
                 "dependencyDigest": digest([row for row in graph if row["name"] != "amplifier-unified"])}}
    # Produced only AFTER the full setup audit, never a restart ACK or pointer.
    write_private(root / QUALIFICATION, json.dumps(proof), owned_root=root)
    return {"ok": True, "version": value["version"], "revision": value["revision"],
            "packageCount": len(value["graph"]), "modelRequests": 0}


def delivery_proof(home):
    """Read first-install qualification, bound to this exact consumer bootstrap."""
    from .windows_files import address, directory
    from .app_component_graph import digest, validate_graph
    home = address(home)
    root = home.parent
    if home != root / "app":
        return None
    root, receipt = installation(root)
    with directory(root, owned_root=root) as owned:
        proof = json.loads(owned.read_private(QUALIFICATION, limit=256 * 1024))
    if (not isinstance(proof, dict) or set(proof) != {
            "schema", "receiptDigest", "installation", "qualification"}
            or type(proof["schema"]) is not int or proof["schema"] != 1
            or proof["receiptDigest"] != _receipt_digest(receipt)):
        raise ValueError("The consumer setup qualification changed.")
    binding = proof["installation"]
    if (not isinstance(binding, dict) or set(binding) != {"prefix", "digest"}
            or not isinstance(binding["prefix"], str)
            or binding["prefix"] != str(root / "tools/amplifier-unified")
            or not isinstance(binding["digest"], str)
            or not re.fullmatch("[a-f0-9]{64}", binding["digest"])):
        raise ValueError("The consumer audit installation binding is missing or invalid.")
    graph = validate_graph(receipt["graph"])
    qualification = proof["qualification"]
    if (not isinstance(qualification, dict) or set(qualification) != {
            "app", "artifact", "extras", "dependencyDigest"}
            or qualification["artifact"] != receipt["artifact"]
            or [qualification["app"]] != [row for row in graph if row["name"] == "amplifier-unified"]
            or qualification["dependencyDigest"] != digest(
                [row for row in graph if row["name"] != "amplifier-unified"])):
        raise ValueError("The consumer setup graph qualification changed.")
    return proof


# Setup names the source store of a new installation "s". Python imports module
# source from that folder by ordinary paths, and with the earlier name the
# deepest default module does not fit a Windows path at the longest
# installation folder that setup accepts (tests/test_windows_path_budget.py).
# An installation that has the earlier folder keeps its store there: nothing is
# moved, renamed or downloaded again.
SOURCE_STORE, EARLIER_SOURCE_STORE = "s", "source-store"


def source_store(root):
    """This installation's source store folder, checked like its other private folders."""
    from .windows_files import directory
    try:
        with directory(root / EARLIER_SOURCE_STORE, owned_root=root, create=False):
            return root / EARLIER_SOURCE_STORE
    except FileNotFoundError:
        with directory(root / SOURCE_STORE, owned_root=root, create=False):
            return root / SOURCE_STORE


def environment(root, value):
    from .windows_files import directory
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith(("UV_", "PIP_")) or name.upper() in {
                "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}:
            del env[name]
    env.update({"UV_NO_CONFIG": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    roots = {"AMPLIFIER_WEB_HOME": "app", "AMPLIFIER_HOME": "shared",
             "AMPLIFIER_SESSION_STATE_HOME": "ownership",
             "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": "capture",
             "AMPLIFIER_UNIFIED_IMPORT_HOME": "import", "UV_TOOL_DIR": "tools",
             "UV_TOOL_BIN_DIR": "tool-bin", "UV_CACHE_DIR": "cache",
             "UV_PYTHON_INSTALL_DIR": "python", "UV_PYTHON_BIN_DIR": "python-bin",
             "TEMP": "temp", "TMP": "temp"}
    for name, suffix in roots.items():
        with directory(root / suffix, owned_root=root, create=False):
            pass
        env[name] = str(root / suffix)
    env["AMPLIFIER_SOURCE_STORE"] = str(source_store(root))
    from .host_dependencies import private_shell
    shell = private_shell(root / "app")
    env["PATH"] = os.pathsep.join([*([shell] if shell else []),
        str(Path(value["uv"]).parent), str(Path(value["git"]).parent),
        str(root / "tool-bin"), str(root / "tools/amplifier-unified/Scripts"),
        str(Path(sys._base_executable).parent), env.get("PATH", "")])
    return env


async def launch(root, *, open_browser=True, progress=None):
    from .windows_installation import (AddressUnavailable, address_message, change_address,
                                       prepare_background, owner_notice, review_browser_trust)
    root, value = installation(root)
    env = environment(root, value)
    os.environ.update(env)  # Own shortcut process only; no saved settings.
    launcher = root / "tools/amplifier-unified/Scripts/amplifier-unified.exe"
    created = []

    def identity():
        # First setup only, in THIS process: the root that Windows is asked to
        # trust below is this exact in-memory certificate, and its signing key
        # is gone when this call returns. Existing material is never replaced.
        from . import windows_tls
        from .deployment import load_server_config
        created.append(windows_tls.create_identity(root / "app", load_server_config(root / "app")))
    try:
        result = await prepare_background(root, launcher, env, identity=identity, progress=progress)
    except AddressUnavailable as problem:
        # Another owner holds the saved address. Never adopt or stop it; only
        # the person's own Yes moves this installation to a new address.
        if not open_browser:
            raise  # A non-interactive caller gets the refusal, not a dialog.
        if not owner_notice(address_message(problem), confirm=True):
            return {"background": False, "address": {"available": False, "reason": problem.kind}}
        await change_address(root, launcher, env)
        result = await prepare_background(root, launcher, env, identity=identity, progress=progress)
    if open_browser:
        # Never silently import a CA or tell a user to bypass a browser warning.
        from . import browser_entry
        from .deployment import load_server_config
        config = load_server_config(root / "app")
        urls, pin = browser_entry._owner_probe_targets(root / "app", config)
        if pin is None:
            raise RuntimeError("The installation's private HTTPS identity is unavailable.")
        fresh = next((value for value in created if value is not None), None)
        if not await asyncio.to_thread(lambda: review_browser_trust(root, urls, pin, fresh=fresh)):
            return {**result, "browser": {"opened": False, "reason": "certificate-review-pending"}}
        # The normal owner entry controls browser authentication and trust
        # behavior. Do not print or expose its transient grant in this process.
        from .updates import process
        try:
            await process(str(launcher), "--data-dir", str(root / "app"),
                          "--workspace", str(root / "workspace"), "open", env=env, timeout=30)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            owner_notice("Amplifier is running in the background, but browser entry did not finish. "
                         "Use the installation's retained status information to inspect the result. "
                         "No certificate trust or security policy was changed.")
    return result


_UNCHANGED = "Amplifier was not uninstalled, and nothing was changed. You can keep using it.\n\n"
_STOPPED = "To keep using Amplifier, open it from the Start menu."
_UNFINISHED = {
    # Before the first step: nothing was asked yet, or the question itself failed.
    "preparation": _UNCHANGED + "Amplifier could not check its own installation files. "
                   "Restart this PC, then uninstall again.",
    "stop-app": "Amplifier could not stop its background app.\n\n"
                "Wait a few minutes or restart this PC, then select Stop Amplifier again.",
    "status": "Amplifier could not read its status.\n\nRestart this PC, then select Amplifier status again.",
    # The steps of an uninstall, in their order.
    "entries-check": _UNCHANGED + "Its entries in the Start menu or the Apps list are in use, or they were "
                     "changed after setup. If another Amplifier message is open, answer it first. "
                     "Otherwise restart this PC, then uninstall again.",
    "task-read": _UNCHANGED + "Amplifier could not read its start-at-sign-in entry from Windows Task "
                 "Scheduler. Restart this PC, then uninstall again.",
    "stop": "Amplifier was not uninstalled, because it did not stop. Nothing was removed.\n\n"
            "Wait a few minutes or restart this PC, then uninstall again.",
    "task-remove": "Amplifier has stopped, but it is not uninstalled yet. Windows did not remove its "
                   "start-at-sign-in entry, and nothing else was removed.\n\n"
                   "Uninstall again to finish. " + _STOPPED,
    "entries": "Amplifier has stopped and no longer starts when you sign in, but some of its entries in "
               "the Start menu or the Apps list could not be removed.\n\n"
               "Uninstall again to finish. " + _STOPPED,
}
_UNFINISHED["apps-entry"] = _UNFINISHED["shortcuts"] = _UNFINISHED["entries"]


def entry_notice(purpose, entry, retry, *, listed=True):
    """The person's text when Amplifier did not confirm its start-at-sign-in entry.

    `purpose` is `start`, `status`, `stop` or `uninstall`; `entry` is the task's
    name in Task Scheduler. The check cannot say why the entry differs (a
    changed setting, another installation, a renamed Windows account), so the
    text does not say that someone changed it. With `retry`, Windows gave no
    answer about the entry's account: that can pass, and nothing is deleted.
    `listed` is False when Amplifier is not in the Start menu: the setup file
    then opens it.
    """
    opening = {"start": "Amplifier could not start.",
               "status": "Amplifier could not read its status.",
               "stop": "Amplifier did not stop its background app.",
               "uninstall": "Amplifier was not uninstalled, and nothing was changed."}[purpose]
    opens = ("Open Amplifier from the Start menu: Amplifier Unified." if listed else
             "Run the Amplifier setup file again.")
    again = {"start": opens, "status": opens, "stop": "Select Stop Amplifier again.",
             "uninstall": "Uninstall Amplifier again."}[purpose]
    if retry:
        return (opening + "\n\n"
                "Windows could not check the account of Amplifier's start-at-sign-in entry, so "
                "Amplifier could not confirm that the entry belongs to this installation.\n\n"
                "1. If your work or school manages this PC, connect it to that network.\n"
                "2. Restart this PC.\n"
                "3. " + again)
    cause = ("Amplifier could not confirm that its start-at-sign-in entry in Windows belongs to "
             "this installation. It uses and changes only an entry that it can confirm.")
    if purpose == "stop":
        return opening + "\n\n" + cause + "\n\nTo stop the app now, sign out of Windows or restart this PC."
    return (opening + "\n\n" + cause + "\n\n"
            f"The entry is the task named {entry} in Windows Task Scheduler. If you have another "
            "Amplifier installation for this Windows account, the entry can belong to it. Then do "
            "not delete the entry.\n\n"
            "Otherwise delete the entry. Amplifier then no longer starts when you sign in"
            + (".\n" if purpose == "uninstall" else ", until it makes a new entry in step 5.\n") +
            "1. Open the Start menu, type Task Scheduler and open it.\n"
            "2. Select Task Scheduler Library.\n"
            f"3. Select the task named {entry}, press the Delete key and confirm.\n"
            "4. Restart this PC.\n"
            "5. " + again
            + ("" if purpose == "uninstall" else " It makes a new entry. Your chats and settings stay."))


class Unfinished(RuntimeError):
    """A Status, Stop or Uninstall step that did not finish; `notice` is the person's text.

    The uninstall steps run in an order that keeps the installation usable:
    nothing the person needs to open, stop or uninstall again is removed before
    the app has stopped and its sign-in task is gone.
    """

    def __init__(self, step, error):
        super().__init__(step)
        self.step, self.error = step, error

    @property
    def notice(self):
        from .windows_service import TaskNotConfirmed
        code = getattr(self.error, "winerror", None)
        if isinstance(self.error, TaskNotConfirmed):
            # The step's own text would name a wrong cause and a wrong next step.
            text = entry_notice({"status": "status", "stop-app": "stop"}.get(self.step, "uninstall"),
                                self.error.entry, self.error.retry)
        else:
            text = _UNFINISHED.get(self.step, "Amplifier could not finish this.\n\nRestart this PC, then try again.")
        return text + f"\n\nReference: {self.step}, {type(self.error).__name__}" + (f" {code}" if code else "")


def _record(root, operation, step, error):
    """Keep one small private note of the step that failed. Never raises.

    Nothing else records why a windowless maintenance step failed. The note
    goes beside the app's other service records. The installation folder is
    the second choice: while a conversation worker is alive, Windows refuses a
    new or replaced file directly in that folder (sharing violation).
    """
    import time
    import traceback
    from .deployment import write_private
    note = json.dumps({
        "schema": 1, "operation": operation, "step": step,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "errorType": type(error).__name__, "winerror": getattr(error, "winerror", None),
        "detail": str(error)[:300],
        "where": [f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                  for frame in traceback.extract_tb(error.__traceback__)[-4:]]})
    for folder in (root / "app" / "config" / "services", root):
        try:
            write_private(folder / RECORD, note, owned_root=root)
            return True
        except (OSError, ValueError, RuntimeError):
            continue
    return False


def _trusted_root(root):
    """This installation's own certificate (DER) if Windows trusts it for this person, else None.

    Only a key-less identity qualifies: setup added that root with the person's
    Yes. An earlier setup's identity added nothing and is never touched here.
    """
    from . import windows_tls, windows_trust
    try:
        identity = windows_tls.keyless_identity(root / "app")
        if identity is not None and windows_trust.contains(identity[0]):
            return identity[0]
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _forget_root(certificate):
    """Ask Windows once to delete this installation's certificate. Never raises.

    Returns `none` (nothing of this installation is trusted), `removed`,
    `absent`, `declined` or `blocked`. Windows itself asks the person; its
    default answer is No. Every outcome is a finished uninstall: a root that
    stays has no signing key anywhere and names only this PC. One attempt, no
    elevation, never the Local Machine store.
    """
    from . import windows_trust
    if certificate is None:
        return "none"
    try:
        return windows_trust.remove_root(certificate)
    except (OSError, ValueError, RuntimeError):
        return "blocked"


async def _uninstall(root):
    """Stop the app and remove its sign-in task first; remove the Windows entries last.

    A failed step leaves every entry that the person needs to open, stop or
    uninstall again. Nothing is retried, force-stopped or elevated.

    The earlier order removed the entries first and then rewrote their record
    in the installation folder. Windows refuses that write while a conversation
    worker is alive, so the app was never asked to stop and kept running with no
    entry left. Workers end with the app's normal stop, which now comes first.
    """
    from . import windows_consumer_shell as shell
    from .installation_health import windows_working
    from .windows_installation import owner_notice
    from .windows_service import uninstall
    from .windows_trust import ROOT_NAME
    # Read-only: say in the first question what Windows will ask at the end.
    certificate = await asyncio.to_thread(_trusted_root, root)
    if not owner_notice(
            "Uninstall Amplifier?\n\n"
            "Amplifier will stop and will no longer start when you sign in. Its entries in the "
            "Start menu and the Apps list will be removed.\n\n"
            "Your chats, settings, accounts and the program files stay in this folder:\n" + str(root)
            + (f'\n\nAt the end, Windows asks whether to delete the certificate "{ROOT_NAME}". '
               "Select Yes there to remove it." if certificate else ""),
            confirm=True):
        return {"ok": True, "cancelled": True}
    working = await windows_working(root / "app")
    if working and not owner_notice(
            ("Amplifier is working on a chat right now." if working == 1 else
             f"Amplifier is working on {working} chats right now.") + "\n\n"
            "Select Yes to stop that work and uninstall now.\n"
            "Select No to keep Amplifier installed. Uninstall again when the work has finished.",
            confirm=True):
        return {"ok": True, "cancelled": True}

    def remove():
        at, result = ["entries-check"], None

        def mark(name):
            at[0] = name
        try:
            if (root / shell.FILE).exists():
                # Check and pin every entry first: a changed entry refuses before
                # the app stops. Remove the entries only when the task is gone.
                with shell.removal(root, progress=mark) as finish:
                    uninstall(root / "app", progress=mark)
                    result = finish()
            else:
                uninstall(root / "app", progress=mark)
                result = {}
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            if result is None:
                raise Unfinished(at[0], error) from error
            result.setdefault("receiptError", error)  # Everything was removed; only tidying failed.
        return result

    try:
        result = await asyncio.to_thread(remove)
    except Unfinished as stopped:
        _record(root, "uninstall", stopped.step, stopped.error)
        raise
    if result.get("receiptError") is not None:
        _record(root, "uninstall", "receipt", result["receiptError"])
    # Last, with the app stopped and its entries gone: the trusted certificate.
    # Its bytes are in app\config\tls, so this comes before any file removal.
    trust = await asyncio.to_thread(_forget_root, certificate)
    if sys.stdout is None:
        # No advice to delete the folder by hand: it holds the person's chats,
        # and File Explorer can refuse its longest paths.
        owner_notice("Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
                     "Your chats, settings, accounts and the program files are still in this folder:\n" + str(root)
                     + (f'\n\nWindows kept the certificate "{ROOT_NAME}". It works only for Amplifier on this PC.'
                        if trust in {"declined", "blocked"} else ""))
    return {"ok": True, "operation": "uninstall", "userDataRetained": True,
            "installationFilesRetained": True, "certificate": trust}


async def maintenance(root, operation):
    """Status, Stop and Uninstall from the Start menu or the Apps list.

    Every failure leaves as `Unfinished`, with a plain cause and next step.
    """
    from .windows_installation import owner_notice
    from .installation_health import status
    from .windows_service import command
    step = {"status": "status", "stop": "stop-app", "uninstall": "preparation"}.get(operation)
    if step is None:
        raise ValueError("Unknown consumer operation.")
    checked = None
    try:
        checked, value = installation(root)
        os.environ.update(environment(checked, value))
        if operation == "status":
            result = await status(checked / "app")
            if sys.stdout is None:
                entry = result.get("entry")
                owner_notice(entry_notice("status", entry["name"], entry["retry"]) if entry
                             else result["message"])
            return result
        if operation == "uninstall":
            return await _uninstall(checked)
        if not owner_notice("Stop Amplifier's background app?", confirm=True):
            return {"ok": True, "cancelled": True}
        await asyncio.to_thread(command, "stop", checked / "app")
        return {"ok": True, "operation": operation, "userDataRetained": True,
                "installationFilesRetained": True}
    except Unfinished:
        raise  # An uninstall step; already recorded.
    except (OSError, ValueError, RuntimeError, TimeoutError, metadata.PackageNotFoundError) as error:
        # Write a record only into a folder that passed the installation check.
        if checked is not None and operation != "status":
            _record(checked, operation, step, error)
        raise Unfinished(step, error) from error

def integrate(root):
    """Setup-only OS entries after source/payload/graph and dependency checks."""
    root, value = installation(root)
    if delivery_proof(root / "app") is None:
        raise ValueError("The consumer payload audit has not qualified this installation.")
    from .windows_consumer_shell import install
    return install(root, value["version"])


def start_failure(root, step=None, error=None):
    """One plain cause and next step when the shortcut could not start the app.

    `step` is the last step that the start named; `error` is what ended it.
    """
    from .windows_consumer_shell import listed
    from .windows_service import TaskNotConfirmed
    has_entries = listed(root)
    if isinstance(error, TaskNotConfirmed):
        # The start-at-sign-in entry exists and Amplifier does not use it. This
        # is not a failed download, and a restart alone does not change it.
        return entry_notice("start", error.entry, error.retry, listed=has_entries)
    next_step = ("open Amplifier from the Start menu: Amplifier Unified" if has_entries else
                 "run the Amplifier setup file again. It keeps your chats and settings")
    if step == "prepare":
        return ("Amplifier could not prepare the remaining parts of the app. It downloads them from the internet.\n\n"
                "Check that this PC is connected to the internet. Then " + next_step + "." +
                (" You do not need to install it again." if has_entries else ""))
    return "Amplifier could not start.\n\nRestart Windows. Then " + next_step + "."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--audit", action="store_true")
    operations.add_argument("--integrate", action="store_true")
    operations.add_argument("--status", action="store_true")
    operations.add_argument("--stop", action="store_true")
    operations.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    steps = []
    try:
        operation = next((name for name in ("status", "stop", "uninstall") if getattr(args, name)), None)
        result = (audit(args.root) if args.audit else integrate(args.root) if args.integrate
                  else asyncio.run(maintenance(args.root, operation))
                  if operation else asyncio.run(launch(args.root, open_browser=not args.no_open,
                                                      progress=steps.append)))
        if sys.stdout is not None:
            print(json.dumps(result))
    except Unfinished as stopped:
        from .windows_installation import owner_notice
        if sys.stdout is None:
            owner_notice(stopped.notice)
        else:
            print(json.dumps({"ok": False, "step": stopped.step, "errorType": type(stopped.error).__name__}))
        raise SystemExit(1) from None
    except (OSError, ValueError, RuntimeError, TimeoutError, metadata.PackageNotFoundError) as error:
        from .windows_installation import owner_notice
        starting = not (args.audit or args.integrate or args.status or args.stop or args.uninstall)
        message = None
        if starting:
            try:
                message = start_failure(args.root, steps[-1] if steps else None, error)
            except (OSError, ValueError, RuntimeError):
                pass
        if sys.stdout is None:
            owner_notice(message or
                         "Amplifier could not complete this operation. Its private setup/service evidence "
                         "is retained. No automatic retry, force-stop or security-policy change was attempted.")
        else:
            print(json.dumps({"ok": False, "errorType": type(error).__name__,
                              **({"message": message} if message else {})}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()