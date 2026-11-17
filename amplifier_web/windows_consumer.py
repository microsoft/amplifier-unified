"""Installed consumer entry; native setup owns acquisition, not this shortcut.

The entry only composes the existing installer/service/browser boundaries. It
never downloads on reopen, copies accounts or force-stops a host. Its one trust
step asks Windows, with the person's own confirmation, to trust this
installation's key-less local certificate; it never changes trust silently.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
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


async def browser_opened(command, env, *, limit=None):
    """Run the owner's `open` command once; True only when it confirmed the browser.

    The confirmation is a line of its output, never its exit status. On
    Windows a command that an app generation takes over ends with status 0 for
    this caller, whatever it did; a first start that updates the app is such a
    case. The command is never run a second time.

    `open` itself waits for a host that is restarting, and needs about 10
    seconds more for its last two requests. The limit here adds 50 seconds
    for the start of the command (the check of the generation that takes it
    over has its own limit of 30 seconds) and for the browser call.

    False means "not confirmed", not "not opened": after the limit, a command
    that a generation took over still runs and can open the browser later.
    """
    from . import browser_entry
    from .updates import process
    if limit is None:
        limit = browser_entry.HOST_WAIT + 60
    try:
        output = await process(*command, "open", env=env, timeout=limit)
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return False
    return browser_entry.OPENED in str(output).splitlines()


async def launch(root, *, open_browser=True, progress=None):
    from .windows_installation import (AddressUnavailable, address_message, change_address,
                                       prepare_background, owner_notice, review_browser_trust)
    from . import windows_uninstall
    root, value = installation(root)
    launcher = root / "tools/amplifier-unified/Scripts/amplifier-unified.exe"
    created = []

    def identity():
        # First setup only, in THIS process: the root that Windows is asked to
        # trust below is this exact in-memory certificate, and its signing key
        # is gone when this call returns. Existing material is never replaced.
        from . import windows_tls
        from .deployment import load_server_config
        created.append(windows_tls.create_identity(root / "app", load_server_config(root / "app")))
    # The start step and an uninstall never run at the same time: a start must
    # not register the app again while its folder is being deleted.
    with _one_at_a_time(root):
        if windows_uninstall.unfinished(root):
            raise Refused("removing")  # Never start on a folder that may be partly deleted.
        env = environment(root, value)
        os.environ.update(env)  # Own shortcut process only; no saved settings.
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
        if not await browser_opened((launcher, "--data-dir", root / "app",
                                     "--workspace", root / "workspace"), env):
            # Say only what is known here: the browser was not confirmed. It
            # can still open, so the person looks for a tab before a new start.
            # The same next step as a failed start: the Start menu only when
            # its entry exists (after an uninstall, setup adds it again).
            from .windows_consumer_shell import listed
            owner_notice("Amplifier may not have opened in your browser.\n\n"
                         "Look for an Amplifier tab in your browser. If there is none, " + (
                "open Amplifier from the Start menu: Amplifier Unified. "
                "If this message appears again, restart this PC." if listed(root) else
                "run the Amplifier setup file again. It keeps your chats and settings."))
            return {**result, "browser": {"opened": False, "reason": "not-confirmed"}}
        return {**result, "browser": {"opened": True}}
    return result


# What an uninstall knows before its first change: it changed nothing. It does not say what the folder
# holds or whether Amplifier is installed (another window can have begun or finished a removal), and not
# that the person can keep using Amplifier (with a journal in the folder the launcher refuses, and Open
# can need what failed here).
_UNCHANGED = "This uninstall stopped before it changed anything.\n\n"
_STOPPED = "To keep using Amplifier, open it from the Start menu."
_UNFINISHED = {
    # Before the first step: nothing was asked yet, or the question itself failed.
    "preparation": _UNCHANGED + "Amplifier could not check its own installation files. "
                   "Restart this PC, then uninstall again.",
    # The installation is checked. Windows did not show a question of this uninstall. Every question
    # comes before the first change.
    "questions": _UNCHANGED + "Amplifier could not show one of its questions. Restart this PC, then uninstall again.",
    # A failure inside the uninstall that no step and no question names. This program does not know
    # whether it came before or after a change, so this text says neither. Where the person can
    # uninstall again depends on which entries are left, as in the text `removing`.
    "uninstall": "Amplifier could not finish this uninstall.\n\n"
                 "Restart this PC. Then select Uninstall Amplifier in the Start menu or in the Windows Apps list. "
                 "If Amplifier is in neither, it is already uninstalled.",
    "stop-app": "Amplifier could not stop its background app.\n\n"
                "Wait a few minutes or restart this PC, then select Stop Amplifier again.",
    "status": "Amplifier could not read its status.\n\nRestart this PC, then select Amplifier status again.",
    # The steps of an uninstall, in their order.
    "entries-check": _UNCHANGED + "Amplifier's entries in the Start menu or the Apps list are in use, or they were "
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
_AGAIN = ("Close other programs or restart this PC. Then uninstall Amplifier again to finish. "
          "Until then Amplifier does not open.")
_UNFINISHED.update({
    # Nothing failed: another Amplifier window is at work, or a removal did not finish.
    "busy": "Amplifier is being opened or uninstalled in another window right now. Nothing was changed.\n\n"
            "If another Amplifier message is open, answer it first. Otherwise wait a minute, then try again.",
    # The installation's lock itself could not be taken or read.
    "lock": _UNCHANGED + "Amplifier could not find out whether it is being opened or uninstalled in another "
            "window. Restart this PC, then uninstall again.",
    "removing": "Amplifier is being removed from this PC, and the removal did not finish. "
                "Amplifier does not open until it has finished.\n\n"
                "To finish, select Uninstall Amplifier in the Start menu or in the Windows Apps list. "
                "If Amplifier is in neither, it is already uninstalled.",
    # The steps that only the delete choice has, in their order.
    "folder": _UNCHANGED + "Amplifier could not check its own folder, so it deleted nothing. "
              "Restart this PC, then uninstall again. If this message returns, select No in the first "
              "question: Amplifier is then uninstalled, and its folder stays.",
    # The step `journal` failed. This program does not know whether a journal is in the folder now: the
    # write can fail before or after the file has its name, a program can take the name away, and Windows
    # can refuse the look for it. With a journal the launcher refuses; without one it does not refuse for
    # that reason. So neither text of this step says that Amplifier opens, and neither says that it does
    # not. The first says what this uninstall deleted, not what the folder holds: a program can have
    # deleted an earlier journal.
    "journal": "Amplifier has stopped and no longer starts when you sign in, but it could not begin to delete "
               "its folder. This uninstall deleted nothing in the folder.\n\n"
               "Wait a minute or restart this PC, then uninstall again. Amplifier might not open until then.",
    # The same step when a journal was in the folder before this uninstall wrote its own: an earlier
    # removal began (a second uninstall, or another window during a question), so files can be deleted.
    "journal-again": "Amplifier could not finish its removal from this PC.\n\n"
                     "Close other programs or restart this PC. Then uninstall Amplifier again to finish. "
                     "Do not open Amplifier before that: its folder can be partly deleted.",
    "data": "Amplifier has stopped, but some of your chats, settings or saved accounts could not be deleted. "
            "A program may still use them.\n\n" + _AGAIN,
    # Every other failure after the deletion began, and every other failure of an uninstall that found a
    # journal in the folder. This program saw the journal: at its start, at the step that failed, or
    # directly after it wrote it.
    "removal": "Amplifier could not finish its removal from this PC.\n\n" + _AGAIN,
})


def entry_notice(purpose, entry, retry, *, listed=True):
    """The person's text when Amplifier did not confirm its start-at-sign-in entry.

    `purpose` is `start`, `status`, `stop`, `uninstall` or `removal` (an
    uninstall that goes on with a removal that began earlier: the app has
    stopped, and data can already be deleted); `entry` is the task's
    name in Task Scheduler. The check cannot say why the entry differs (a
    changed setting, another installation, a renamed Windows account), so the
    text does not say that someone changed it. The cause sentence makes no
    claim about what Amplifier changed: a start can make an entry and then
    not confirm it. (Only the uninstall opening says that it changed nothing:
    an uninstall reads the entry before it changes anything.) With `retry`,
    Windows gave no answer about the entry's account: that can pass, and
    nothing is deleted.
    `listed` is False when Amplifier is not in the Start menu: the setup file
    then opens it.
    """
    opening = {"start": "Amplifier could not start.",
               "status": "Amplifier could not read its status.",
               "stop": "Amplifier did not stop its background app.",
               "uninstall": "This uninstall stopped before it changed anything.",
               "removal": "Amplifier could not finish its removal from this PC. "
                          "Until it has finished, Amplifier does not open."}[purpose]
    opens = ("Open Amplifier from the Start menu: Amplifier Unified." if listed else
             "Run the Amplifier setup file again.")
    again = {"start": opens, "status": opens, "stop": "Select Stop Amplifier again.",
             "uninstall": "Uninstall Amplifier again.", "removal": "Uninstall Amplifier again."}[purpose]
    removes = purpose in ("uninstall", "removal")
    cause = ("Windows could not check the account of Amplifier's start-at-sign-in entry, so "
             "Amplifier could not confirm that the entry belongs to this installation." if retry else
             "Amplifier could not confirm that its start-at-sign-in entry in Windows belongs to "
             "this installation. It uses only an entry that it can confirm.")
    if purpose == "stop":
        # The person wants the app to stop. That needs no confirmed entry.
        return opening + "\n\n" + cause + "\n\nTo stop the app now, sign out of Windows or restart this PC."
    if retry:
        return (opening + "\n\n" + cause + "\n\n"
                "1. If your work or school manages this PC, connect it to that network.\n"
                "2. Restart this PC.\n"
                "3. " + again)
    return (opening + "\n\n" + cause + "\n\n"
            f"The entry is the task named {entry} in Windows Task Scheduler. If you have another "
            "Amplifier installation for this Windows account, the entry can belong to it. Then do "
            "not delete the entry: use that installation, or uninstall it first.\n\n"
            "Otherwise delete the entry. Amplifier then no longer starts when you sign in"
            + (".\n" if removes else ", until it makes a new entry in step 5.\n") +
            "1. Open the Start menu, type Task Scheduler and open it.\n"
            "2. Select Task Scheduler Library.\n"
            f"3. Select the task named {entry}, press the Delete key and confirm.\n"
            "4. Restart this PC.\n"
            "5. " + again
            + ("" if removes else " It makes a new entry. Your chats and settings stay."))


class Unfinished(RuntimeError):
    """A Status, Stop or Uninstall step that did not finish; `notice` is the person's text.

    The uninstall steps run in an order that keeps the installation usable:
    nothing the person needs to open, stop or uninstall again is removed before
    the app has stopped and its sign-in task is gone.
    """

    def __init__(self, step, error, text=None):
        super().__init__(step)
        # `text` selects the person's text when the step's own text does not
        # fit, for example a step that failed after the deletion began.
        self.step, self.error, self.text = step, error, text or step

    @property
    def notice(self):
        from .windows_service import TaskNotConfirmed
        code = getattr(self.error, "winerror", None)
        if isinstance(self.error, TaskNotConfirmed):
            # The step's own text would name a wrong cause and a wrong next step.
            # After a removal began, "before it changed anything" would say too little.
            text = entry_notice("removal" if self.text == "removal" else
                                {"status": "status", "stop-app": "stop"}.get(self.step, "uninstall"),
                                self.error.entry, self.error.retry)
        else:
            text = _UNFINISHED.get(self.text, "Amplifier could not finish this.\n\nRestart this PC, then try again.")
        return text + f"\n\nReference: {self.step}, {type(self.error).__name__}" + (f" {code}" if code else "")


class Refused(Unfinished):
    """Nothing failed and nothing was changed: Amplifier does not do this now, and says why."""

    def __init__(self, step, error=None):
        super().__init__(step, error or RuntimeError(step))

    @property
    def notice(self):
        return _UNFINISHED[self.step]


class NotShown(Unfinished):
    """The uninstall did its work, and Windows did not show the message that ends it.

    `notice` is what the person must still be told, for one more attempt to show it. It is never the
    text of a failed preparation: that text says that the uninstall stopped before it changed anything.
    """

    def __init__(self, message, error):
        super().__init__("message", error)
        self.message = message

    @property
    def notice(self):
        code = getattr(self.error, "winerror", None)
        return self.message + f"\n\nReference: {self.step}, {type(self.error).__name__}" + (f" {code}" if code else "")


def _last_message(root, text, *, again=None, services):
    """Show the message that ends an uninstall which did its work.

    When Windows does not show it, the uninstall stops here, as before. `again` is the text for the
    one further attempt; without it that is the same text. The note of the failure goes where the
    notes of this uninstall go (`services` as in `_record`).
    """
    from .windows_installation import owner_notice
    try:
        owner_notice(text)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        _record(root, "uninstall", "message", error, services=services)
        raise NotShown(again or text, error) from error


@contextmanager
def _one_at_a_time(root):
    """One start or removal at a time for this installation, across processes."""
    from . import windows_uninstall
    try:
        with windows_uninstall.single(root):
            yield
    except windows_uninstall.Busy as busy:
        raise Refused("busy", busy) from None


def _record(root, operation, step, error, *, services=True):
    """Keep one small private note of the step that failed. Never raises.

    Nothing else records why a windowless maintenance step failed. The note
    goes beside the app's other service records. The installation folder is
    the second choice: while a conversation worker is alive, and its Foundation
    is older than e37a946b, Windows refuses a
    new or replaced file directly in that folder (sharing violation).
    Without `services` the note goes only into the installation folder: the
    app's own folder is the person's data, and it is being deleted.
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
    for folder in (root / "app" / "config" / "services", root) if services else (root,):
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
    from . import windows_consumer_shell as shell, windows_uninstall
    from .installation_health import windows_working
    from .windows_installation import owner_notice
    from .windows_service import uninstall
    from .windows_trust import ROOT_NAME
    # Read-only: say in the first question what Windows will ask at the end.
    certificate = await asyncio.to_thread(_trusted_root, root)
    unchanged = {"ok": True, "cancelled": True}
    unfinished = windows_uninstall.unfinished(root)
    record = windows_uninstall.pending(root) if unfinished else None
    resumed = record is not None and record["choice"] == "delete"
    delete = chosen = False

    def ask(show, text, **options):
        """One question of this uninstall. When Windows does not show it, the uninstall stops and says so.

        Every question comes before the first change, so the text of that stop says that this uninstall
        changed nothing. The note of the failure goes where a failed preparation puts it.
        """
        try:
            return show(text, **options)
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            _record(root, "uninstall", "questions", error)
            raise Unfinished("questions", error) from error
    if resumed:
        # A removal began and did not finish. The journal is not proof that
        # the person chose it: a program of this account can write that file.
        # So the person chooses again, twice, as for a first removal.
        if ask(windows_uninstall.question,
               "The removal of Amplifier from this PC did not finish.\n\n"
               "Select Yes to finish it now. This deletes everything that Amplifier still keeps in this "
               "folder, including chats, settings, saved accounts and stored keys. This cannot be undone.\n"
               + str(root) + "\n\n"
               "Amplifier asks you once more before it deletes anything.\n\n"
               "Select No to change nothing now. Amplifier does not open until the removal has finished.") != "yes":
            return unchanged
        chosen = True
    elif not unfinished and windows_uninstall.completed(root):
        # Only a folder that setup completed as an installation can be deleted.
        answer = ask(
            windows_uninstall.question,
            "Uninstall Amplifier?\n\n"
            "Amplifier will stop and will no longer start when you sign in. Its entries in the "
            "Start menu and the Apps list will be removed.\n\n"
            "Do you also want to delete your chats, settings and saved accounts?\n\n"
            "Select No to keep them. They stay in this folder, together with the program files:\n"
            + str(root) + "\n\n"
            "Select Yes to delete them, together with the program files. Amplifier asks you once more "
            "before it deletes anything.\n\n"
            "Select Cancel to keep Amplifier installed."
            + (f'\n\nDuring the uninstall, Windows asks whether to delete the certificate "{ROOT_NAME}". '
               "That is a separate question." if certificate else ""),
            cancel=True)
        if answer == "cancel":
            return unchanged
        chosen = answer == "yes"
    elif not ask(
            owner_notice,
            "Uninstall Amplifier?\n\n"
            "Amplifier will stop and will no longer start when you sign in. Its entries in the "
            "Start menu and the Apps list will be removed.\n\n"
            # A journal that proves no delete choice is in the folder: an earlier removal can have
            # deleted files. This program does not know what is left, so it says only what it does.
            + ("This uninstall deletes none of your chats, settings, accounts or program files in this folder:\n"
               if unfinished else
               "Your chats, settings, accounts and the program files stay in this folder:\n") + str(root)
            + (f'\n\nAt the end, Windows asks whether to delete the certificate "{ROOT_NAME}". '
               "Select Yes there to remove it." if certificate else ""),
            confirm=True):
        return unchanged
    if chosen:
        # The second choice, immediately before the deletion, in every case.
        if ask(windows_uninstall.question,
               "Delete everything that Amplifier keeps in this folder?\n\n" + str(root) + "\n\n"
               "This deletes all your Amplifier chats, your settings, your saved accounts and your "
               "stored keys. It also deletes the files that Amplifier or you saved in its workspace "
               "folder there. This cannot be undone.\n\n"
               "Select Yes to delete all of that and uninstall Amplifier.\n"
               + ("Select No to change nothing now." if resumed else
                  "Select No to change nothing. Amplifier stays installed.")) != "yes":
            return unchanged
        delete = True
    # A removal that did not finish stopped the app before it deleted anything.
    working = None if resumed else await windows_working(root / "app")
    if working and not ask(
            owner_notice,
            ("Amplifier is working on a chat right now." if working == 1 else
             f"Amplifier is working on {working} chats right now.") + "\n\n"
            "Select Yes to stop that work and uninstall now.\n"
            "Select No to keep Amplifier installed. Uninstall again when the work has finished.",
            confirm=True):
        return unchanged
    if delete:
        return await _delete(root, certificate, resumed=resumed)

    earlier = [unfinished]  # A journal is in the folder: an earlier removal began.

    def remove():
        at, result = ["lock"], None

        def mark(name):
            at[0] = name
        try:
            with _one_at_a_time(root):
                if not unfinished and windows_uninstall.unfinished(root):
                    # Another window began to delete this folder after the
                    # question. Its entries must stay for the second uninstall.
                    raise Refused("removing")
                mark("entries-check")
                if (root / shell.FILE).exists():
                    # Check and pin every entry first: a changed entry refuses before
                    # the app stops. Remove the entries only when the task is gone.
                    with shell.removal(root, progress=mark) as finish:
                        uninstall(root / "app", progress=mark)
                        result = finish()
                else:
                    uninstall(root / "app", progress=mark)
                    result = {}
        except Refused:
            raise  # Another window starts or removes this installation; nothing was changed.
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            if result is None:
                # A journal is in the folder: it was there at the start and proves no delete choice, or
                # another window began a removal after the question (seen now; the look under the lock
                # above did not run when the lock itself failed). The launcher then refuses to open
                # Amplifier, and a step's own text can say that it opens.
                earlier[0] = earlier[0] or windows_uninstall.unfinished(root)
                raise Unfinished(at[0], error, "removal" if earlier[0] else None) from error
            result.setdefault("receiptError", error)  # Everything was removed; only tidying failed.
        return result

    try:
        result = await asyncio.to_thread(remove)
    except Refused:
        raise
    except Unfinished as stopped:
        _record(root, "uninstall", stopped.step, stopped.error, services=not earlier[0])
        raise
    if result.get("receiptError") is not None:
        _record(root, "uninstall", "receipt", result["receiptError"])
    # Last, with the app stopped and its entries gone: the trusted certificate.
    # Its bytes are in app\config\tls, so this comes before any file removal.
    trust = await asyncio.to_thread(_forget_root, certificate)
    if sys.stdout is None:
        # No advice to delete the folder by hand: it holds the person's chats,
        # and File Explorer can refuse its longest paths.
        _last_message(
            root, "Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
            + ("This uninstall deleted none of your chats, settings, accounts or program files in "
               "this folder:\n" if unfinished else
               "Your chats, settings, accounts and the program files are still in this folder:\n")
            + str(root)
            + (f'\n\nWindows kept the certificate "{ROOT_NAME}". It works only for Amplifier on this PC.'
               if trust in {"declined", "blocked"} else ""), services=not unfinished)
    return {"ok": True, "operation": "uninstall", "userDataRetained": True,
            "installationFilesRetained": True, "certificate": trust}


def _last_files(root, foreign, opening):
    """What stays when the last step did not run: program files, none of the person's data."""
    # Advice to delete the folder only when nothing in it is someone else's.
    return (opening + " They are in this folder, and they hold none of your chats, settings or accounts:\n"
            + str(root) + ("\n\nYou can delete that folder." if foreign == [] else ""))


def _removed_notice(root, foreign, trust, *, last=True):
    """The one confirmation of a deleted installation. It says only what was observed.

    Without `last` the last step does not follow this text: Windows did not show the confirmation, so
    the uninstall stopped before that step, and the program files that it runs from stay.
    """
    from .windows_trust import ROOT_NAME
    text = ("Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
            "Your chats, settings, saved accounts and stored keys are deleted.\n\n")
    if not last:
        text += _last_files(root, foreign, "Amplifier did not remove its last program files.")
        if trust in {"declined", "blocked"}:
            text += f'\n\nWindows kept the certificate "{ROOT_NAME}". It works only for Amplifier on this PC.'
        return text
    text += "When you close this message, Amplifier starts to remove its last program files. "
    if foreign:
        text += ("The folder itself stays, because it holds entries that are not Amplifier's. "
                 "They were not changed:\n" + "\n".join(foreign[:5])
                 + (f"\nand {len(foreign) - 5} more" if len(foreign) > 5 else "") + "\n\nThe folder:\n" + str(root))
    elif foreign is None:  # The folder could not be listed at the end.
        text += "This folder can still exist afterwards:\n" + str(root)
    else:
        text += ("If this folder still exists a minute later, you can delete it. "
                 "It holds none of your data:\n" + str(root))
    if trust in {"declined", "blocked"}:
        text += f'\n\nWindows kept the certificate "{ROOT_NAME}". It works only for Amplifier on this PC.'
    return text


async def _delete(root, certificate, *, resumed):
    """Uninstall and delete the installation folder: the person chose that, twice.

    `windows_uninstall.remove` has the order and the reasons for it. Before
    its journal exists, a failed step leaves the installation as it was, as in
    an uninstall that keeps the folder. After that, a failed step leaves a
    folder that Amplifier does not start from, and a second uninstall
    finishes the removal.
    """
    from . import windows_uninstall
    from .windows_service import uninstall
    # `earlier`: a journal was in the folder before this uninstall wrote its own, so an earlier removal
    # began and files can already be deleted. `resumed` is what the folder showed before the questions.
    # Another window can begin a removal while a question waits, and this path does not look again
    # before it goes on. So it looks when it must choose a text: before the write of its own journal,
    # and at a step before that which failed. Only the person's text and the place of the failure note
    # depend on these looks.
    at, earlier = ["lock"], [resumed]

    def mark(name):
        at[0] = name
        if name == "journal" and windows_uninstall.unfinished(root):
            earlier[0] = True

    def remove():
        try:
            return windows_uninstall.remove(
                root, progress=mark, stop=lambda: uninstall(root / "app", progress=mark),
                certificate=lambda: _forget_root(certificate))
        except windows_uninstall.Busy as busy:
            raise Refused("busy", busy) from None
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            begun = at[0] in windows_uninstall.AFTER_JOURNAL
            if at[0] == "journal":
                text = "journal-again" if earlier[0] else None  # The two texts of this step say why.
            else:
                if not begun and windows_uninstall.unfinished(root):
                    earlier[0] = True  # This uninstall has written no journal yet: it is another window's.
                text = "data" if at[0] == "data" else "removal" if begun or earlier[0] else None
            raise Unfinished(at[0], error, text) from error

    try:
        done = await asyncio.to_thread(remove)
    except Refused:
        raise
    except Unfinished as stopped:
        # Never a note in the app's own folder once that folder is being deleted.
        _record(root, "uninstall", stopped.step, stopped.error,
                services=not earlier[0] and stopped.step not in windows_uninstall.AFTER_JOURNAL)
        raise
    foreign = done["foreign"]
    if sys.stdout is None:
        # When Windows does not show this confirmation, the uninstall stops before its last step, as
        # before. The further attempt then says that the last program files stay.
        _last_message(root, _removed_notice(root, foreign, done["certificate"]),
                      again=_removed_notice(root, foreign, done["certificate"], last=False), services=False)
    # Last: the two folders that this running program runs from. After this
    # step the process imports nothing more and ends: its own files are gone.
    try:
        last = await asyncio.to_thread(windows_uninstall.finish, root, done["token"], short=done["short"])
        started = True
    except (OSError, ValueError, RuntimeError):
        last, started = None, False
        if sys.stdout is None:
            _last_message(root, _last_files(root, foreign, "Amplifier could not remove its last program files."),
                          services=False)
    # `afterExit`: the step after exit was started; nobody observed that it
    # finished. `programLeft`: program files that could be neither deleted
    # nor moved, so the folder stays.
    return {"ok": True, "operation": "uninstall", "userDataRetained": False,
            "certificate": done["certificate"], "kept": foreign or [],
            "afterExit": started, "programLeft": last["left"] if last else None}


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
        from . import windows_uninstall
        if not windows_uninstall.unfinished(checked):
            os.environ.update(environment(checked, value))
        elif operation != "uninstall":
            # The folder may be partly deleted. Only an uninstall goes on from
            # here, and it needs none of the folders that `environment` checks.
            raise Refused("removing")
        if operation == "status":
            result = await status(checked / "app")
            if sys.stdout is None:
                entry = result.get("entry")
                owner_notice(entry_notice("status", entry["name"], entry["retry"]) if entry
                             else result["message"])
            return result
        if operation == "uninstall":
            # The installation is checked. A step or a question of the uninstall that fails leaves it as
            # `Unfinished`, with its own text. Any other failure in it has no name of its own.
            step = "uninstall"
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