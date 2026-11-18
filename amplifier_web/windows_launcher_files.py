"""Compare the files that the Windows launcher's own processes can load.

FILES and PACKAGES list the package files and third-party distributions used
when an app generation is active. scripts/windows_launcher_files.py writes
the lists and the keys of DYNAMIC. A person decides each dynamic load.
When the list test fails, run python scripts/windows_launcher_files.py --write,
then decide each new DYNAMIC entry and run the command again.

The Python interpreter, pyvenv.cfg and the Scripts start programs are not
compared. The interpreter is the installation's private one, shared by the
launcher and every app version. uv makes the start programs from
entry_points.txt, which is compared.
"""
from pathlib import Path
import re

# BEGIN LAUNCHER FILE LISTS
FILES = ('amplifier_web/__init__.py',
 'amplifier_web/app_component_graph.py',
 'amplifier_web/app_features.py',
 'amplifier_web/app_installation_proof.py',
 'amplifier_web/app_release_artifacts.py',
 'amplifier_web/app_updates.py',
 'amplifier_web/application_generations.py',
 'amplifier_web/auth.py',
 'amplifier_web/browser_entry.py',
 'amplifier_web/cli.py',
 'amplifier_web/deployment.py',
 'amplifier_web/deployment_service.py',
 'amplifier_web/host/__init__.py',
 'amplifier_web/host/config.py',
 'amplifier_web/host_dependencies.py',
 'amplifier_web/host_pam.py',
 'amplifier_web/installation_health.py',
 'amplifier_web/manual_app_handoff.py',
 'amplifier_web/provider_sources.json',
 'amplifier_web/reset.py',
 'amplifier_web/reset_retention.py',
 'amplifier_web/runtime_retention.py',
 'amplifier_web/session_files.py',
 'amplifier_web/shared_settings.py',
 'amplifier_web/status_protocol/__init__.py',
 'amplifier_web/status_protocol/cli.py',
 'amplifier_web/status_protocol/hook.py',
 'amplifier_web/status_protocol/publisher.py',
 'amplifier_web/status_protocol/reader.py',
 'amplifier_web/status_protocol/schema.py',
 'amplifier_web/tls.py',
 'amplifier_web/update_command_details.py',
 'amplifier_web/update_diagnostics.py',
 'amplifier_web/update_readiness.py',
 'amplifier_web/updates.py',
 'amplifier_web/windows_app_recovery.py',
 'amplifier_web/windows_consumer.py',
 'amplifier_web/windows_consumer_shell.py',
 'amplifier_web/windows_files.py',
 'amplifier_web/windows_installation.py',
 'amplifier_web/windows_jobs.py',
 'amplifier_web/windows_launch_guard.py',
 'amplifier_web/windows_launcher_task.py',
 'amplifier_web/windows_ports.py',
 'amplifier_web/windows_read_files.py',
 'amplifier_web/windows_removal.py',
 'amplifier_web/windows_service.py',
 'amplifier_web/windows_start.py',
 'amplifier_web/windows_task_runtime.py',
 'amplifier_web/windows_tls.py',
 'amplifier_web/windows_trust.py',
 'amplifier_web/windows_uninstall.py')

PACKAGES = ('aiohappyeyeballs',
 'aiohttp',
 'aiosignal',
 'attrs',
 'cffi',
 'cryptography',
 'filelock',
 'frozenlist',
 'idna',
 'multidict',
 'propcache',
 'pycparser',
 'pyyaml',
 'yarl')

DYNAMIC = {
    'amplifier_web/application_generations.py::delegate::-m::hand_over(python, ["-I", "-m", "amplifier_web", *args])': (),  # Runs the app generation.
    'amplifier_web/application_generations.py::delegate::__file__::if not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):': (),  # Compares the package path.
    "amplifier_web/application_generations.py::verify_candidate::-c::result = subprocess.run([str(python), '-I', '-c', GRAPH_PROBE],": (),  # Reads metadata in the app generation.
    'amplifier_web/cli.py::_parse::-m::command.add_argument("--model", "-m")': (),  # A command-line option, not a program.
    'amplifier_web/deployment_service.py::_generated_unit_matches::-m::elif re.fullmatch(r"python(?:\\d+(?:\\.\\d+)*)?", executable) and args[1:3] == ["-m", "amplifier_web"]:': (),  # Compares saved arguments.
    "amplifier_web/host/config.py::<module>::__file__::_KNOWN_PROVIDER_SOURCES = json.loads((Path(__file__).parents[1] / 'provider_sources.json').read_text())": ('amplifier_web/provider_sources.json',),  # Reads bundled provider names.
    'amplifier_web/reset.py::<module>::__file__::_spec = importlib.util.spec_from_file_location("reset_retention", Path(__file__).with_name("reset_retention.py"))': ('amplifier_web/reset_retention.py',),  # Loads the standalone reset helper.
    'amplifier_web/reset.py::<module>::spec_from_file_location::_spec = importlib.util.spec_from_file_location("reset_retention", Path(__file__).with_name("reset_retention.py"))': ('amplifier_web/reset_retention.py',),  # Loads the standalone reset helper.
    'amplifier_web/reset.py::_repair::-m::command(prefix / "bin/python", "-I", "-m", "amplifier_web",': (),  # Runs the newly installed app.
    'amplifier_web/reset.py::post_checks::-m::result = command(prefix / "bin/python", "-I", "-m", "amplifier_web", "--data-dir", home,': (),  # Checks the newly installed app.
    'amplifier_web/reset.py::prepare_saved_configurations::-c::command(prefix / "bin/python", "-I", "-c",': (),  # Runs the newly installed app.
    'amplifier_web/reset.py::verify::-c::command(python, "-I", "-c", "from amplifier_web.app_updates import PROBE; exec(PROBE)",': (),  # Probes the newly installed app.
    'amplifier_web/reset_retention.py::Retention::__file__::spec = importlib.util.spec_from_file_location("reset", Path(__file__).with_name("reset.py"))': ('amplifier_web/reset.py',),  # Loads the standalone reset command.
    'amplifier_web/reset_retention.py::Retention::spec_from_file_location::spec = importlib.util.spec_from_file_location("reset", Path(__file__).with_name("reset.py"))': ('amplifier_web/reset.py',),  # Loads the standalone reset command.
    "amplifier_web/update_readiness.py::running_identity::__file__::if Path(distribution.locate_file('amplifier_web')).resolve() == Path(__file__).parent.resolve():": (),  # Compares the package path.
    'amplifier_web/windows_app_recovery.py::restore::-c::result = subprocess.run([str(prefix / "Scripts/python.exe"), "-I", "-c", components.GRAPH_PROBE],': (),  # Reads metadata; imports no app code.
    'amplifier_web/windows_consumer.py::audit::-c::result = subprocess.run([sys.executable, "-I", "-c", GRAPH_PROBE],': (),  # Reads metadata; imports no app code.
    'amplifier_web/windows_consumer.py::installation::__file__::if (Path(sys.prefix) != prefix or Path(dist.locate_file("amplifier_web")) != Path(__file__).parent': (),  # Compares the package path.
    'amplifier_web/windows_consumer_shell.py::entries::-m::args = subprocess.list2cmdline(["-I", "-m", "amplifier_web.windows_consumer", "--root", str(root)])': ('amplifier_web/windows_consumer.py',),  # Starts the launcher shortcut handler.
    'amplifier_web/windows_consumer_shell.py::registration_values::-m::[str(python), "-I", "-m", "amplifier_web.windows_consumer", "--root", str(root), "--uninstall"]),': ('amplifier_web/windows_consumer.py',),  # Starts the launcher uninstall handler.
    'amplifier_web/windows_jobs.py::WindowsOwnedProcess::__file__::str(Path(__file__).resolve().with_name("windows_launch_guard.py")),': ('amplifier_web/windows_launch_guard.py',),  # Starts the standalone process guard.
    'amplifier_web/windows_launcher_task.py::helper_xml::-m::"-I", "-m", "amplifier_web.windows_launcher_refresh", "--automatic", payload])': (),  # The helper task's action. It runs with the app generation's Python.
    'amplifier_web/windows_service.py::TaskSpec::-m::command = subprocess.list2cmdline(["-I", "-m", "amplifier_web.windows_service", "--run", self.encoded()])': ('amplifier_web/windows_service.py',),  # Starts the launcher supervisor.
    'amplifier_web/windows_service.py::_launch_owned_host::-m::[python, "-I", "-m", "amplifier_web.windows_service", "--host", spec.encoded()],': (),  # Runs the app generation's host.
}
# END LAUNCHER FILE LISTS


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _site(prefix):
    prefix = Path(prefix)
    infos = [*prefix.glob("lib/python*/site-packages/amplifier_unified-*.dist-info"),
             *prefix.glob("Lib/site-packages/amplifier_unified-*.dist-info")]
    if len(infos) != 1 or not infos[0].is_dir():
        raise OSError(f"Cannot find one amplifier-unified site-packages in {prefix}")
    return infos[0].parent, infos[0]


def _read(path, unreadable):
    try:
        return path.read_bytes()
    except OSError:
        unreadable.add(str(path))
        return None


def _versions(site, unreadable):
    versions = {}
    try:
        entries = sorted(site.iterdir())
    except OSError:
        unreadable.add(str(site))
        return versions
    for info in entries:
        # Read metadata only for the distributions in PACKAGES.
        if not info.name.endswith(".dist-info"):
            continue
        name = _normalize(info.name[:-len(".dist-info")].rsplit("-", 1)[0])
        if name not in PACKAGES:
            continue
        content = _read(info / "METADATA", unreadable)
        try:
            lines = content.decode("utf-8").splitlines() if content is not None else []
            metadata_name = next(line.partition(":")[2].strip() for line in lines if line.startswith("Name:"))
            version = next(line.partition(":")[2].strip() for line in lines if line.startswith("Version:"))
            if _normalize(metadata_name) != name or not version or name in versions:
                raise ValueError("Ambiguous distribution metadata")
            versions[name] = version
        except (UnicodeError, ValueError, StopIteration):
            unreadable.add(str(info / "METADATA"))
    return versions


def _without_version(content):
    if content is None:
        return None
    return b"".join(line for line in content.splitlines(keepends=True)
                    if not re.match(rb"^\s*__version__\s*(?::[^=]+)?=(?!=)", line))


def differs(launcher, app) -> dict:
    """Compare two installed environment prefixes, without importing either app."""
    unreadable, files, packages, sites = set(), [], [], []
    for prefix in (launcher, app):
        try:
            sites.append(_site(prefix))
        except OSError:
            unreadable.add(str(prefix))
            sites.append(None)
    if all(sites):
        left, right = sites
        for path in FILES:
            older = _read(left[0] / path, unreadable)
            newer = _read(right[0] / path, unreadable)
            if path == "amplifier_web/__init__.py":
                older, newer = _without_version(older), _without_version(newer)
            if older is None or newer is None or older != newer:
                files.append(path)
        older = _read(left[1] / "entry_points.txt", unreadable)
        newer = _read(right[1] / "entry_points.txt", unreadable)
        if older is None or newer is None or older != newer:
            files.append("amplifier_unified-*.dist-info/entry_points.txt")
        older, newer = (_versions(site[0], unreadable) for site in sites)
        packages = [name for name in PACKAGES if older.get(name) != newer.get(name)]
    return {"differs": bool(files or packages or unreadable), "files": files[:20],
            "packages": packages[:20], "unreadable": sorted(unreadable)[:20]}