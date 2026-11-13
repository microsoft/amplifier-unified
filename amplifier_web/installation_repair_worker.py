"""Detached, standard-library-only installation repair.

The controller copies this file and its three inspected siblings outside the
serving tool prefix, writes a private immutable plan and accepted receipt, then
runs ``base_python -I -S worker.py OPERATION_FOLDER``. Only operation.json is the
mutable authority; a non-accepted operation is never executed again.
"""
from __future__ import annotations

from contextlib import contextmanager, suppress
import hashlib
import hmac
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


EXTRAS = frozenset({
    "native-desktop", "tui", "managed-browser", "decisions-openai",
    "decisions-azure", "decisions-jev", "decisions-local",
})
HEALTH_WAIT = 60
_FAILURE_POLICY = None

# Run inside the installed interpreter, in isolated mode. No app imports or
# third-party graph reader are needed to observe the resolved environment.
GRAPH_PROBE = r'''import importlib.metadata as m,json,re,sys
from pathlib import Path
rows=[]
for d in m.distributions():
 name=re.sub(r"[-_.]+","-",d.metadata["Name"].lower())
 root=Path(d.locate_file("")).resolve()
 assert root.is_relative_to(Path(sys.prefix).resolve())
 row={"name":name,"version":d.version,"direct":json.loads(d.read_text("direct_url.json") or "null")}
 if name=="amplifier-unified":
  raw=Path(d.locate_file("amplifier_web/release-source.json")).read_bytes()
  assert len(raw)<=16384
  row["stamp"]=json.loads(raw)
 rows.append(row)
print(json.dumps(sorted(rows,key=lambda row:row["name"]),sort_keys=True))
'''

PROTOCOL_PROBE = r'''import json
from amplifier_web.installation_repair import PROTOCOL
assert type(PROTOCOL) is int and PROTOCOL==1
print(json.dumps({"protocol":PROTOCOL}))
'''


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Noncanonical repair path")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Linked repair path")
    return path


def _owned(info, *, private=False):
    if (info.st_uid != os.getuid() or info.st_mode & 0o022
            or private and info.st_mode & 0o077):
        raise ValueError("Repair state is not private")


def directory(path, *, private=False):
    path = canonical(path)
    info = path.lstat()
    _owned(info, private=private)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Repair directory is not physical")
    return path


def _regular(info, *, private=False, single=True):
    _owned(info, private=private)
    if not stat.S_ISREG(info.st_mode) or single and info.st_nlink != 1:
        raise ValueError("Repair state is not a regular file")


def _bytes(path, *, private=False, limit=4_000_000, single=True):
    path = canonical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _regular(os.fstat(stream.fileno()), private=private, single=single)
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Repair input exceeds limit")
    return value


def read(path):
    """Read private JSON without following links (also used by the controller)."""
    return json.loads(_bytes(path, private=True))


def sync_directory(path):
    fd = os.open(canonical(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic(path, raw, *, exclusive=False):
    path = canonical(path)
    directory(path.parent, private=True)
    if path.exists():
        _bytes(path, private=True)
        if exclusive:
            raise FileExistsError("Repair input already exists")
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # link/unlink publishes without replacing any concurrent creator.
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        else:
            canonical(path)
            os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def write(path, value, *, exclusive=False):
    """Publish private JSON atomically and durably; never print its contents."""
    _atomic(path, (json.dumps(value, sort_keys=True) + "\n").encode(), exclusive=exclusive)


@contextmanager
def lock(path):
    """Nonblocking private operation lock; never unlink its stable inode."""
    import fcntl

    path = canonical(path)
    directory(path.parent, private=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _regular(os.fstat(fd), private=True)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _sibling(name):
    path = canonical(Path(__file__).absolute().with_name(name + ".py"))
    # This is the controller-inspected sibling, not cwd, PYTHONPATH, or an app
    # import from an environment which is about to be moved.
    _bytes(path)
    spec = importlib.util.spec_from_file_location("_installation_repair_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def siblings():
    global _FAILURE_POLICY
    # reset's direct-file branch loads reset_retention from its exact sibling.
    _bytes(canonical(Path(__file__).absolute().with_name("reset_retention.py")))
    reset = _sibling("reset")
    _FAILURE_POLICY = reset
    artifacts = _sibling("app_release_artifacts")
    reset.command = command  # capture *all* helper output, including service calls
    reset._REPORT = None
    return reset, artifacts


class CommandFailed(RuntimeError):
    def __init__(self, facts=None, guidance=None):
        super().__init__("Repair child did not complete successfully")
        self.facts = facts or {}
        self.guidance = guidance


def _installer_failure(stdout, stderr):
    """Only the inspected policy's structured classification crosses this edge."""
    classify = getattr(_FAILURE_POLICY, "installer_failure_facts", None)
    guide = getattr(_FAILURE_POLICY, "installer_failure_guidance", None)
    try:
        value = classify(stdout, stderr) if callable(classify) else {}
        facts = {}
        if isinstance(value, dict):
            for key, pattern in (
                ("reason", r"[a-z][a-z0-9-]{0,63}"),
                ("package", r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}"),
                ("constraint", r"[a-zA-Z0-9_.!<>=~*, +\-]{1,160}"),
            ):
                if isinstance(value.get(key), str) and re.fullmatch(pattern, value[key]):
                    facts[key] = value[key]
        guidance = guide(facts) if callable(guide) else None
        if (not isinstance(guidance, str) or len(guidance) > 1500
                or any(ord(char) < 32 and char not in "\n\t" for char in guidance)):
            guidance = None
        return CommandFailed(facts, guidance)
    except Exception:
        return CommandFailed()


def command(*args, env=None, capture=True, check=True, timeout=120):
    """Keep child output private in memory; stop descendants before rollback.

    In particular, CalledProcessError/TimeoutExpired must never escape carrying
    a command line, proxy setting, token, or installer stderr into a receipt.
    """
    process = subprocess.Popen(
        [str(arg) for arg in args], env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    result = subprocess.CompletedProcess([], process.returncode, stdout, stderr)
    if check and result.returncode:
        raise _installer_failure(stdout, stderr)
    return result


def error_type(error):
    name = type(error).__name__
    return name if name in {
        "ValueError", "TypeError", "KeyError", "FileNotFoundError", "PermissionError",
        "OSError", "RuntimeError", "TimeoutError", "TimeoutExpired", "CommandFailed",
        "BlockingIOError", "InterruptedError",
    } else "Exception"


def _digest(path):
    return hashlib.sha256(_bytes(path, limit=64_000_000)).hexdigest()


def _exists(path):
    return path.exists() or path.is_symlink()


def _mkdir(path):
    canonical(path).mkdir(mode=0o700)
    directory(path, private=True)
    sync_directory(path.parent)


def _move(source, destination):
    canonical(source.parent)
    canonical(destination.parent)
    if _exists(destination):
        raise FileExistsError("Recovery destination exists")
    source.rename(destination)
    sync_directory(source.parent)
    sync_directory(destination.parent)


def _launcher_identity(path):
    canonical(path.parent)
    if path.is_symlink():
        return ("link", os.readlink(path))
    if path.exists():
        info = path.lstat()
        _regular(info)
        return ("file", stat.S_IMODE(info.st_mode), _digest(path))
    return None


def file_identity(path):
    """Controller's entry identity: lstat, literal link, then target bytes."""
    path = Path(path)
    canonical(path.parent)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        if info.st_uid != os.getuid():
            raise ValueError("Launcher owner changed")
        link = os.readlink(path)
    else:
        _regular(info, single=False)
        link = None
    raw = _bytes(path.resolve(strict=True), single=False)
    return {"path": str(path), "stat": [info.st_dev, info.st_ino],
            "link": link, "sha256": hashlib.sha256(raw).hexdigest()}


def bootstrap_identity(prefix):
    """Byte-identical hash recipe to application_generations.bootstrap_identity."""
    prefix = directory(prefix)
    folders = [*prefix.glob("lib/python*/site-packages/amplifier_unified-*.dist-info"),
               *prefix.glob("Lib/site-packages/amplifier_unified-*.dist-info")]
    if len(folders) != 1:
        raise ValueError("Application bootstrap identity could not be verified")
    folder = directory(folders[0])
    digest = hashlib.sha256(_bytes(folder / "METADATA", single=False))
    direct = folder / "direct_url.json"
    digest.update(_bytes(direct, single=False) if _exists(direct) else b"<index>")
    return {"prefix": str(prefix), "digest": digest.hexdigest()}


def _path_snapshot(path):
    canonical(path.parent)
    if not _exists(path):
        return None
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        if info.st_uid != os.getuid():
            raise ValueError("Recovery member owner changed")
        return {"stat": [info.st_dev, info.st_ino], "link": os.readlink(path)}
    _owned(info)
    if stat.S_ISDIR(info.st_mode):
        receipt = path / "uv-receipt.toml"
        return {"stat": [info.st_dev, info.st_ino], "bootstrap": bootstrap_identity(path)["digest"],
                "uvReceipt": _digest(receipt) if _exists(receipt) else None}
    return {"stat": [info.st_dev, info.st_ino], "sha256": _digest(path),
            "mode": stat.S_IMODE(info.st_mode)}


def _check_service_process(plan):
    service = plan["service"]
    if sys.platform == "linux":
        result = command(
            "systemctl", "--user", "show", "amplifier-unified.service",
            "--property=MainPID", "--property=FragmentPath", "--property=DropInPaths",
            "--property=NeedDaemonReload", env=plan["env"],
        )
        properties = dict(row.split("=", 1) for row in result.stdout.splitlines() if "=" in row)
        if (properties.get("MainPID") != str(service["pid"])
                or properties.get("FragmentPath") != service["path"]
                or properties.get("DropInPaths") != ""
                or properties.get("NeedDaemonReload") != "no"):
            raise ValueError("Loaded service definition changed")
    started = command("ps", "-p", str(service["pid"]), "-o", "lstart=", env=plan["env"]).stdout.strip()
    owner = command("ps", "-p", str(service["pid"]), "-o", "uid=", env=plan["env"]).stdout.strip()
    if (not started or started != service["processStart"].strip()
            or owner != str(plan["uid"])):
        raise ValueError("Service process lifetime or owner changed")


def _assert_other_processes(plan):
    """Ignore this helper and the authenticated old host, not other profiles."""
    prefix, home = Path(plan["toolRoot"]) / "amplifier-unified", Path(plan["home"])
    # Idle workers in this data directory retire through the normal generated
    # service shutdown. The post-stop full process check and exclusive home
    # lease still require every one gone before moving a path. Here detect a
    # different profile using the shared bootstrap before interrupting our host.
    roots = (str(prefix) + "/",)
    output = command("ps", "-axo", "pid=,command=", env=plan["env"]).stdout
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        if int(parts[0]) in {os.getpid(), plan["service"]["pid"]}:
            continue
        text = parts[1]
        if any(root in text for root in roots):
            raise ValueError("The installation is used by another process")


def _service_pid(plan):
    service = plan["service"]
    stop, _, loaded = service["commands"]
    if stop == ["systemctl", "--user", "stop", "amplifier-unified.service"]:
        result = command(
            "systemctl", "--user", "show", "amplifier-unified.service",
            "--property=MainPID", "--value", env=plan["env"], check=False,
        )
        if result.returncode or not re.fullmatch(r"\d+\s*", result.stdout):
            raise ValueError("Service process is not observable")
        return int(result.stdout)
    if (sys.platform == "darwin" and loaded ==
            ["launchctl", "print", f"gui/{os.getuid()}/com.microsoft.amplifier-unified"]):
        result = command(*loaded, env=plan["env"], check=False)
        if result.returncode:
            # A failed print is not by itself proof of absence.
            domain = command("launchctl", "print", f"gui/{os.getuid()}",
                             env=plan["env"], check=False)
            exact_label = re.search(
                r"(?<![\w.-])com\.microsoft\.amplifier-unified(?![\w.-])", domain.stdout)
            if domain.returncode or exact_label:
                raise ValueError("Service process is not observable")
            return 0
        matches = re.findall(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
        if len(matches) > 1:
            raise ValueError("Service process is ambiguous")
        return int(matches[0]) if matches else 0
    raise ValueError("Unrecognized managed service")


def service_state(plan):
    try:
        return "running" if _service_pid(plan) else "stopped"
    except Exception:
        return "unknown"


def _health_url(url, pin):
    value = urlsplit(url)
    if (value.scheme not in {"https", "http"} or value.hostname not in {"127.0.0.1", "::1"}
            or value.username is not None or value.password is not None
            or value.path != "/api/health" or value.query or value.fragment
            or value.scheme == "https" and not re.fullmatch("[a-f0-9]{64}", pin or "")):
        raise ValueError("Health requires a local pinned listener")
    # Accessing port also rejects malformed/out-of-range port text.
    return value, value.port or (443 if value.scheme == "https" else 80)


def _health_get(url, pin, token):
    value, port = _health_url(url, pin)
    if value.scheme == "https":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # exact leaf pin, checked BEFORE auth
        connection = http.client.HTTPSConnection(value.hostname, port, timeout=2, context=context)
    else:
        connection = http.client.HTTPConnection(value.hostname, port, timeout=2)
    try:
        connection.connect()
        if value.scheme == "https":
            observed = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(observed, pin):
                raise ValueError("Listener pin mismatch")
        connection.request("GET", "/api/health", headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        raw = response.read(65_537)
        if response.status != 200 or len(raw) > 65_536:
            raise ValueError("Health response is not valid")
        return json.loads(raw)
    finally:
        connection.close()


def authenticated_health(plan, *, successor=False, wait=0):
    token = _bytes(Path(plan["home"]) / "config/auth/control-token", private=True, limit=4096).decode().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("Owner credential is not valid")
    expected = plan["target"] if successor else plan["runningIdentity"]
    data_identity = hashlib.sha256(plan["home"].encode()).hexdigest()
    deadline = time.monotonic() + wait
    while True:
        for url in plan["health"]["urls"]:
            try:
                value = _health_get(url, plan["health"]["pin"], token)
                if (not isinstance(value, dict) or value.get("ok") is not True
                        or value.get("app") != "amplifier-unified"
                        or value.get("dataIdentity") != data_identity
                        or any(value.get(key) != expected[key] for key in ("version", "revision"))):
                    continue
                instance = value.get("instanceId")
                if successor:
                    if (not isinstance(instance, str) or not re.fullmatch("[a-f0-9]{32}", instance)
                            or instance == plan["runningIdentity"]["instanceId"]):
                        continue
                    repair = value.get("installationRepair")
                    if (not isinstance(repair, dict) or repair.get("operationId") != plan["operationId"]
                            or type(repair.get("protocol")) is not int or repair["protocol"] != 1):
                        continue
                elif instance != expected["instanceId"]:
                    continue
                return value
            except (OSError, ValueError, http.client.HTTPException):
                pass
        if time.monotonic() >= deadline:
            raise ValueError("Authenticated host health could not be verified")
        time.sleep(min(.5, max(0, deadline - time.monotonic())))


def _check_pointer(plan):
    path = Path(plan["home"]) / "updates/application-active.json"
    canonical(path)
    observed = {"exists": path.exists(), "sha256": _digest(path) if path.exists() else None}
    if observed != plan["pointer"]:
        raise ValueError("Application pointer changed")


def _check_live(plan, reset):
    home = directory(plan["home"])
    info = home.stat()
    if (plan["uid"] != os.getuid() or [info.st_dev, info.st_ino] != plan["dataStat"]):
        raise ValueError("Data directory identity changed")
    launcher = Path(plan["binRoot"]) / "amplifier-unified"
    if file_identity(launcher) != plan["launcher"]:
        raise ValueError("Public launcher changed")
    prefix = Path(plan["toolRoot"]) / "amplifier-unified"
    if (bootstrap_identity(prefix) != plan["bootstrap"]
            or _digest(prefix / "uv-receipt.toml") != plan["uvReceiptDigest"]):
        raise ValueError("Bootstrap installation changed")
    if _digest(Path(plan["service"]["path"])) != plan["service"]["sha256"]:
        raise ValueError("Service definition changed")
    commands = reset.service_commands(home)
    normalized = [list(row) if row is not None else None for row in commands] if commands else None
    if normalized != plan["service"]["commands"] or _service_pid(plan) != plan["service"]["pid"]:
        raise ValueError("Managed service identity changed")
    _check_service_process(plan)
    _check_pointer(plan)


def validate_plan(plan, operation, folder):
    for key in ("operationId", "previewId", "hostInstanceId"):
        if (not isinstance(plan[key], str) or not re.fullmatch("[A-Za-z0-9_-]{1,128}", plan[key])
                or operation[key] != plan[key]):
            raise ValueError("Repair operation identity changed")
    if folder.name != plan["operationId"]:
        raise ValueError("Repair folder identity changed")
    home = directory(plan["home"])
    if folder.parent != home / "installation-repair":
        raise ValueError("Repair folder is outside the private namespace")
    for key in ("toolRoot", "binRoot"):
        directory(plan[key])
    prefix = Path(plan["toolRoot"]) / "amplifier-unified"
    directory(prefix)
    if Path(__file__).resolve().is_relative_to(prefix):
        raise ValueError("Repair helper is inside the installation")
    for key in ("uv", "basePython"):
        # Executable symlinks (not state/sibling links) are normal for Homebrew
        # and system Python. The controller owns their exact selection.
        path = canonical(Path(plan[key]).resolve(strict=True))
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("Repair executable is unavailable")
        if key == "basePython" and (path.is_relative_to(prefix) or path.is_relative_to(home)):
            raise ValueError("Base Python is inside the repair target")
    extras = plan["extras"]
    if (not isinstance(extras, list) or any(not isinstance(item, str) for item in extras)
            or len(extras) != len(set(extras)) or not set(extras) <= EXTRAS):
        raise ValueError("Repair feature selection is invalid")
    for key in ("probe", "dependencyProbe"):
        if not isinstance(plan[key], str) or not plan[key] or len(plan[key]) > 100_000:
            raise ValueError("Repair probe is invalid")
    if type(plan["service"]["pid"]) is not int or plan["service"]["pid"] <= 0:
        raise ValueError("Repair requires the exact running service")
    if plan["target"]["source"] != plan["artifact"]["url"]:
        raise ValueError("Repair source changed")
    def version(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("Repair requires stable package versions")
        return tuple(int(part) for part in value.split("."))
    if version(plan["target"]["version"]) < version(plan["runningIdentity"]["version"]):
        raise ValueError("Repair cannot downgrade the running application")
    artifact = canonical(plan["artifact"]["path"])
    if not artifact.is_relative_to(folder):
        raise ValueError("Repair artifact is outside its operation")
    if not isinstance(plan["env"], dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            and "=" not in key and "\0" not in key + value for key, value in plan["env"].items()):
        raise ValueError("Repair child environment is invalid")
    if not isinstance(plan["health"]["urls"], list) or not 1 <= len(plan["health"]["urls"]) <= 8:
        raise ValueError("Repair requires local health listeners")
    for url in plan["health"]["urls"]:
        _health_url(url, plan["health"]["pin"])


def qualify(plan, python, env):
    result = command(python, "-I", "-c", plan["probe"], *plan["extras"], env=env)
    rows = [line.split("=", 1)[1] for line in result.stdout.splitlines()
            if line.startswith("AMPLIFIER_UPDATE_PROBE=")]
    facts = json.loads(rows[0]) if len(rows) == 1 else None
    if (not isinstance(facts, dict) or facts.get("ok") is not True
            or facts.get("isolated") is not True or facts.get("version") != plan["target"]["version"]):
        raise ValueError("Installed package qualification failed")
    command(python, "-I", "-c", plan["dependencyProbe"], *plan["extras"], env=env)
    protocol = command(python, "-I", "-c", PROTOCOL_PROBE, env=env)
    value = json.loads(protocol.stdout)
    if not isinstance(value, dict) or type(value.get("protocol")) is not int or value["protocol"] != 1:
        raise ValueError("Candidate does not support installation repair protocol 1")


def _app_source(plan, row):
    stamp = row.get("stamp")
    if (row.get("version") != plan["target"]["version"]
            or not isinstance(stamp, dict) or type(stamp.get("schema")) is not int
            or stamp != {
                "schema": 1, "repository": "microsoft/amplifier-unified",
                "version": plan["target"]["version"], "revision": plan["target"]["revision"],
            }):
        raise ValueError("Installed app release provenance changed")
    direct = row.get("direct")
    if (not isinstance(direct, dict) or set(direct) != {"url", "archive_info"}
            or direct["url"] != Path(plan["artifact"]["path"]).as_uri()
            or not isinstance(direct["archive_info"], dict)):
        raise ValueError("Installed app archive source changed")
    archive = direct["archive_info"]
    hashes = archive.get("hashes")
    if hashes is None and isinstance(archive.get("hash"), str):
        algorithm, separator, value = archive["hash"].partition("=")
        hashes = {algorithm: value} if separator else None
    # uv local-wheel installations can deliberately leave archive_info empty.
    # The artifact was independently downloaded/checksummed and is reverified
    # at each install boundary; an optional recorded hash corroborates it, but
    # absence is not conflicting provenance.
    if hashes is None and archive == {}:
        hashes = {"sha256": plan["artifact"]["sha256"]}
    if not isinstance(hashes, dict) or hashes.get("sha256") != plan["artifact"]["sha256"]:
        raise ValueError("Installed app archive checksum changed")
    # Only the exact verified local archive is represented as official delivery.
    # Dependency direct URLs remain untouched, including their source identity.
    return {**row, "direct": {
        "url": plan["artifact"]["url"], "archive_info": {"hashes": {"sha256": hashes["sha256"]}},
    }}


def installed_graph(plan, python, env):
    result = command(python, "-I", "-c", GRAPH_PROBE, env=env)
    if len(result.stdout) > 4_000_000:
        raise ValueError("Installed graph exceeds limit")
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Installed graph is empty")
    normalized = []
    for row in rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get("name"), str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"])
                or not isinstance(row.get("version"), str) or not row["version"]
                or not isinstance(row.get("direct"), (dict, type(None)))):
            raise ValueError("Installed graph is malformed")
        normalized.append(_app_source(plan, row) if row["name"] == "amplifier-unified" else row)
    names = [row["name"] for row in normalized]
    if len(names) != len(set(names)) or names.count("amplifier-unified") != 1:
        raise ValueError("Installed graph contains duplicate or missing distributions")
    return sorted(normalized, key=lambda row: row["name"])


def _installed_provenance(plan, prefix):
    """Read app provenance without executing a possibly foreign interpreter."""
    prefix = directory(prefix)
    folders = list(prefix.glob("lib/python*/site-packages/amplifier_unified-*.dist-info"))
    if len(folders) != 1:
        raise ValueError("Cannot prove ownership of a partial installation")
    folder = directory(folders[0])
    # Reading METADATA directly also binds the claimed version, not just a stamp.
    from email.parser import BytesParser
    metadata = BytesParser().parsebytes(_bytes(folder / "METADATA", single=False))
    if re.sub("[-_.]+", "-", metadata.get("Name", "").lower()) != "amplifier-unified":
        raise ValueError("Foreign installed distribution")
    site = folder.parent
    _app_source(plan, {
        "name": "amplifier-unified", "version": metadata.get("Version"),
        "direct": json.loads(_bytes(folder / "direct_url.json", single=False)),
        "stamp": json.loads(_bytes(site / "amplifier_web/release-source.json", single=False, limit=16_384)),
    })
    return _path_snapshot(prefix)


def _settled_paths(plan, prefix, launcher, pointer, old_launcher):
    """Classify only settled owned outputs; never adopt an unexpected pointer."""
    if _exists(pointer):
        raise ValueError("A foreign application pointer appeared")
    current_prefix = _installed_provenance(plan, prefix) if _exists(prefix) else None
    current_launcher = _path_snapshot(launcher)
    if current_launcher != old_launcher and current_launcher is not None:
        if (not launcher.is_symlink() or Path(os.path.abspath(launcher.parent / os.readlink(launcher)))
                != prefix / "bin/amplifier-unified"):
            raise ValueError("A foreign public launcher appeared")
        # A matching link alone does not prove a present output belongs to this
        # installer; its target must live in the provenance-verified prefix.
        if current_prefix is None:
            raise ValueError("New launcher has no verified installation")
        _bytes((prefix / "bin/amplifier-unified").resolve(strict=True), single=False)
    return current_prefix, current_launcher


def _same_snapshot(path, expected):
    if _path_snapshot(path) != expected:
        raise ValueError("Recovery path changed after inspection")


def candidate(plan, folder, artifacts):
    tools, binaries = folder / "candidate-tools", folder / "candidate-bin"
    for path in (tools, binaries, folder / "constraints.txt", folder / "candidate-graph.json"):
        if _exists(path):
            raise ValueError("Candidate input already exists")
    env = {**plan["env"], "UV_TOOL_DIR": str(tools), "UV_TOOL_BIN_DIR": str(binaries)}
    command(plan["uv"], "tool", "install", "--python", "3.13", "--from",
            artifacts.requirement(plan["artifact"], plan["extras"]), "amplifier-unified",
            env=env, timeout=1800)
    python = tools / "amplifier-unified/bin/python"
    if "managed-browser" in plan["extras"]:
        command(python, "-I", "-m", "playwright", "install", "chromium", env=env, timeout=600)
    qualify(plan, python, env)
    write(folder / "candidate-graph.json", installed_graph(plan, python, env), exclusive=True)
    result = command(plan["uv"], "pip", "freeze", "--python", python, env=env)
    if not result.stdout.strip():
        raise ValueError("Candidate constraints are empty")
    _atomic(folder / "constraints.txt", result.stdout.encode(), exclusive=True)


class Worker:
    def __init__(self, folder, operation):
        self.folder = folder
        self.operation = operation
        self.mutation_started = False

    def receipt(self, status, stage, detail, *, error=None, **facts):
        if error is not None:
            facts["errorType"] = error_type(error)
            if isinstance(error, CommandFailed):
                if error.facts:
                    facts["installerFailure"] = error.facts
                if error.guidance:
                    facts["guidance"] = error.guidance
        guidance = facts.get("guidance") or self.operation.get("guidance")
        if guidance:
            detail += " " + guidance
        self.operation.update(status=status, stage=stage, detail=detail,
                              updatedAt=time.time(), **facts)
        write(self.folder / "operation.json", self.operation)
        return dict(self.operation)

    def swap(self, plan, reset, artifacts):
        """Caller owns home and tool leases. Never remove failed or old copies."""
        home, tools, bins = (Path(plan[key]) for key in ("home", "toolRoot", "binRoot"))
        prefix, launcher = tools / "amplifier-unified", bins / "amplifier-unified"
        pointer = home / "updates/application-active.json"
        backup = self.folder / "backup"
        recovery = reset.tool_recovery_root(tools) / plan["operationId"]
        _mkdir(backup)
        _mkdir(recovery)
        previous = recovery / "previous"
        if (file_identity(launcher) != plan["launcher"]
                or bootstrap_identity(prefix) != plan["bootstrap"]
                or _digest(prefix / "uv-receipt.toml") != plan["uvReceiptDigest"]):
            raise ValueError("Installation entry changed while the service stopped")
        old_prefix = _path_snapshot(prefix)
        old_launcher = _path_snapshot(launcher)
        old_pointer = _path_snapshot(pointer)
        launcher_identity = _launcher_identity(launcher)
        saved_launcher = saved_pointer = None
        if launcher_identity is not None:
            shutil.copy2(launcher, backup / "launcher", follow_symlinks=False)
            if not (backup / "launcher").is_symlink():
                with (backup / "launcher").open("rb") as stream:
                    os.fsync(stream.fileno())
            sync_directory(backup)
            saved_launcher = _path_snapshot(backup / "launcher")
        prefix_moved = pointer_moved = installing = False
        settled = None
        try:
            self.receipt("running", "backing-up", "Saving the previous installation.")
            # Conservatively retain unknown if rename or any subsequent durable
            # progress write escapes before a positively established outcome.
            self.mutation_started = True
            prefix.rename(previous)
            prefix_moved = True
            sync_directory(tools)
            sync_directory(recovery)
            _check_pointer(plan)
            if plan["pointer"]["exists"]:
                _same_snapshot(pointer, old_pointer)
                pointer.rename(backup / "application-active.json")
                pointer_moved = True
                sync_directory(pointer.parent)
                sync_directory(backup)
                saved_pointer = _path_snapshot(backup / "application-active.json")
                if saved_pointer != old_pointer:
                    raise ValueError("Saved application pointer changed")
            self.receipt("running", "reinstalling", "Installing a fresh verified copy.")
            env = {**plan["env"], "UV_TOOL_DIR": str(tools), "UV_TOOL_BIN_DIR": str(bins)}
            # Recheck the exact retained bytes at the installation boundary.
            artifacts.verify(plan["artifact"], plan["target"]["version"], plan["target"]["revision"])
            installing = True
            try:
                command(plan["uv"], "tool", "install", "--force", "--python", "3.13",
                        "--constraint", self.folder / "constraints.txt", "--from",
                        artifacts.requirement(plan["artifact"], plan["extras"]), "amplifier-unified",
                        env=env, timeout=1800)
            finally:
                # The child is settled/reaped before these snapshots. A foreign
                # or unidentifiable partial prefix cannot become ours by naming.
                settled = _settled_paths(plan, prefix, launcher, pointer, old_launcher)
            qualify(plan, prefix / "bin/python", env)
            graph = installed_graph(plan, prefix / "bin/python", env)
            if graph != read(self.folder / "candidate-graph.json"):
                raise ValueError("Fresh installation graph differs from the qualified candidate")
            _same_snapshot(prefix, settled[0])
            _same_snapshot(launcher, settled[1])
            if _exists(pointer):
                raise ValueError("Application pointer unexpectedly appeared")
            self.receipt("running", "reinstalled", "The fresh installation passed its probes.",
                         reinstalled=True)
            return "reinstalled"
        except Exception as error:
            if not prefix_moved:
                self.receipt("failed", "left-stopped",
                             "The service is stopped; no installation paths were moved.",
                             serviceState="stopped", reinstalled=False, error=error)
                return "failed"
            self.receipt("running", "rolling-back", "Restoring the previous installation.",
                         error=error, reinstalled=False)
            try:
                if installing and settled is None:
                    raise ValueError("Installer output ownership could not be verified")
                # Prove every backup and every destination before restoring any
                # path. Same-account noncooperating writers are not sandboxed.
                _same_snapshot(previous, old_prefix)
                if plan["launcher"]["link"] is not None:
                    target = launcher.parent / plan["launcher"]["link"]
                    target = Path(os.path.abspath(target))
                    if not target.is_relative_to(prefix):
                        raise ValueError("Old launcher target is outside its saved installation")
                    if _digest(previous / target.relative_to(prefix)) != plan["launcher"]["sha256"]:
                        raise ValueError("Saved launcher target changed")
                if launcher_identity is not None:
                    _same_snapshot(backup / "launcher", saved_launcher)
                    if _launcher_identity(backup / "launcher") != launcher_identity:
                        raise ValueError("Launcher backup changed")
                if pointer_moved:
                    _same_snapshot(backup / "application-active.json", saved_pointer)
                    if _digest(backup / "application-active.json") != plan["pointer"]["sha256"]:
                        raise ValueError("Saved application pointer digest changed")
                    if _exists(pointer):
                        raise ValueError("A foreign application pointer appeared")
                else:
                    _same_snapshot(pointer, old_pointer)
                expected_prefix, expected_launcher = settled if installing else (None, old_launcher)
                _same_snapshot(prefix, expected_prefix)
                _same_snapshot(launcher, expected_launcher)
                if installing and expected_prefix is not None:
                    _move(prefix, recovery / "failed")
                _same_snapshot(previous, old_prefix)
                _move(previous, prefix)
                if pointer_moved:
                    if _exists(pointer):
                        raise ValueError("Pointer destination changed during restoration")
                    _move(backup / "application-active.json", pointer)
                if installing and expected_launcher != old_launcher:
                    _same_snapshot(launcher, expected_launcher)
                    if _exists(launcher):
                        # Preserve the verified failed launcher, never unlink it.
                        _move(launcher, backup / "failed-launcher")
                    if launcher_identity is not None:
                        if _exists(launcher):
                            raise ValueError("Launcher destination changed during restoration")
                        shutil.copy2(backup / "launcher", launcher, follow_symlinks=False)
                    sync_directory(bins)
                _same_snapshot(prefix, old_prefix)
                if _launcher_identity(launcher) != launcher_identity:
                    raise ValueError("Previous installation restoration is incomplete")
                _check_pointer(plan)
            except Exception as rollback_error:
                self.receipt("unknown", "rollback-incomplete",
                             "Restoration is incomplete. Leave the service stopped and inspect recovery.",
                             serviceState="stopped", reinstalled=False,
                             error=rollback_error)
                return "unknown"
            self.receipt("rolled-back", "rolled-back", "The previous installation was restored.",
                         serviceState="stopped", reinstalled=False)
            return "rolled-back"

    def execute(self, plan, reset, artifacts):
        self.receipt("running", "preflight", "Checking the repair inputs.")
        stopping = stopped = False
        outcome = None
        try:
            validate_plan(plan, self.operation, self.folder)

            def helper_command(*args, env=None, **kwargs):
                return command(*args, env=plan["env"] if env is None else env, **kwargs)

            reset.command = helper_command
            plan_digest = _digest(self.folder / "plan.json")
            _check_live(plan, reset)
            artifacts.verify(plan["artifact"], plan["target"]["version"], plan["target"]["revision"])
            candidate(plan, self.folder, artifacts)
            with reset.repair_lock(reset.tool_recovery_root(Path(plan["toolRoot"]))):
                recovery = reset.tool_recovery_root(Path(plan["toolRoot"])) / plan["operationId"]
                if _exists(recovery) or _exists(self.folder / "backup"):
                    raise ValueError("Recovery destination already exists")
                authenticated_health(plan)
                if _digest(self.folder / "plan.json") != plan_digest:
                    raise ValueError("Repair plan changed")
                _assert_other_processes(plan)
                _check_live(plan, reset)  # last check, immediately before stop
                self.receipt("running", "stopping", "Stopping the managed service.")
                stopping = True
                if reset.stop_service(plan["service"]["commands"]) is False:
                    raise ValueError("Managed service disappeared before stop")
                if _service_pid(plan) != 0:
                    raise ValueError("Managed service did not stop")
                stopped = True
                self.receipt("running", "stopped", "The managed service is stopped.", serviceState="stopped")
                prefix, home = Path(plan["toolRoot"]) / "amplifier-unified", Path(plan["home"])
                reset.assert_no_processes(prefix, home)
                with reset.repair_lock(home):
                    reset.assert_no_processes(prefix, home)
                    outcome = self.swap(plan, reset, artifacts)
                # A successor must be able to take the data lease on startup.
                if outcome in {"unknown", "failed"}:
                    return dict(self.operation)
                try:
                    self.receipt("running" if outcome == "reinstalled" else "rolled-back",
                                 "starting" if outcome == "reinstalled" else "rolled-back",
                                 "Starting the managed service.")
                    reset.start_service(plan["service"]["commands"])
                except Exception as error:
                    return self.receipt(
                        outcome, "restart-incomplete" if outcome == "reinstalled" else "rolled-back",
                        "Installation preserved; the managed service could not be confirmed ready.",
                        serviceState=service_state(plan), healthVerified=False, error=error)
                if outcome == "rolled-back":
                    return self.receipt("rolled-back", "rolled-back",
                                        "The previous installation was restored.",
                                        serviceState=service_state(plan), healthVerified=False)
                return self.post_checks(plan)
        except Exception as error:
            if not stopping:
                return self.receipt("failed", "preflight-failed",
                                    "Repair checks failed; the running installation was not changed.",
                                    error=error)
            state = "stopped" if stopped and outcome is None else service_state(plan)
            return self.receipt(
                "unknown" if outcome or self.mutation_started else "failed",
                "interrupted" if outcome or self.mutation_started else
                "left-stopped" if state == "stopped" else "stop-incomplete",
                "Repair did not complete. Inspect the saved operation before further action.",
                serviceState=state, healthVerified=False, error=error)

    def post_checks(self, plan):
        doctor_ok = health_ok = False
        error = None
        try:
            python = Path(plan["toolRoot"]) / "amplifier-unified/bin/python"
            result = command(python, "-I", "-m", "amplifier_web", "--data-dir", plan["home"],
                             "doctor", "--json", env=plan["env"], check=False)
            value = json.loads(result.stdout)
            doctor_ok = result.returncode == 0 and isinstance(value, dict) and value.get("ok") is True
        except Exception as failure:
            error = error_type(failure)
        try:
            authenticated_health(plan, successor=True, wait=HEALTH_WAIT)
            pid = _service_pid(plan)
            health_ok = pid > 0 and pid != plan["service"]["pid"]
        except Exception as failure:
            error = error_type(failure)
        state = service_state(plan)
        ready = doctor_ok and health_ok and state == "running"
        return self.receipt(
            "ready" if ready else "reinstalled", "ready" if ready else "checks-incomplete",
            "The repaired installation is ready." if ready else
            "Unified was reinstalled, but readiness is not verified. Recovery copies are preserved.",
            reinstalled=True, doctorVerified=doctor_ok, healthVerified=ready,
            serviceState=state, **({"errorType": error} if error else {}))


def run(operation_folder):
    folder = directory(operation_folder, private=True)
    with lock(folder / "worker.lock"):
        operation = read(folder / "operation.json")
        if operation.get("status") != "accepted":
            return operation
        worker = Worker(folder, operation)
        executing = False
        try:
            plan = read(folder / "plan.json")
            reset, artifacts = siblings()
            # This namespace lease spans preflight, replacement and restart.
            namespace = directory(directory(plan["home"]) / "installation-repair", private=True)
            with reset.repair_lock(namespace):
                executing = True
                return worker.execute(plan, reset, artifacts)
        except Exception as error:
            return worker.receipt(
                "unknown" if executing else "failed",
                "interrupted" if executing else "preflight-failed",
                "Repair did not complete. Inspect the saved operation before further action."
                if executing else "Repair checks failed; the running installation was not changed.",
                healthVerified=False, errorType=error_type(error))
        except BaseException as error:
            # Even an interrupted preflight is not replayable. Do not guess at
            # effects, roll back again, or restart after a crash.
            return worker.receipt(
                "unknown", "interrupted", "Repair was interrupted. Inspect its saved state; do not replay.",
                serviceState="unknown", healthVerified=False, errorType=error_type(error))


def main():
    os.umask(0o077)
    if (len(sys.argv) != 2 or not sys.flags.isolated or not sys.flags.no_site
            or os.name != "posix"):
        return 2
    try:
        run(sys.argv[1])
        return 0
    except BaseException:
        # No traceback: its arguments, child output and private plan can contain
        # credentials. A failure to publish a receipt is not fabricated success.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())