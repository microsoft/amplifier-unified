"""Emergency repair must work when the ordinary application cannot import."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from amplifier_web import reset


@pytest.fixture
def repair_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(reset, "service_commands", lambda home: None)
    monkeypatch.setattr(reset, "assert_no_processes", lambda *args: None)
    monkeypatch.setattr(reset, "post_checks", lambda *a, **kw: {"doctor": {"ok": True}, "service status": {"ok": True}})
    home = tmp_path / "data"
    tool = tmp_path / "tools"
    bins = tmp_path / "bin"
    for root in (home, tool, bins):
        root.mkdir()
    prefix = tool / "amplifier-unified"
    prefix.mkdir()
    (prefix / "old").write_text("locally edited install")
    (bins / "amplifier-unified").symlink_to(prefix / "bin/amplifier-unified")
    for name in reset.REGENERABLE:
        (home / name).mkdir()
        (home / name / "broken").write_text("bad cache or pointer")
    for name in ("config", "attachments", "unknown-plugin", "sessions"):
        (home / name).mkdir()
        (home / name / "keep").write_bytes(b"private user data\x00")
    with sqlite3.connect(home / "app.sqlite3") as db:
        db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO state VALUES(1, ?)", (json.dumps({"updates": {"pendingReplacement": True}, "sessions": [{"id": "chat", "text": "preserve"}]}),))
        db.execute("CREATE TABLE state_records(kind TEXT, id TEXT, value TEXT)")
        db.execute("INSERT INTO state_records VALUES('global','updates',?)", (json.dumps({"present": True, "value": {"pendingRestart": True}}),))
        db.execute("INSERT INTO state_records VALUES('session','chat','original bytes')")
    return home, tool, bins


def fake_install(monkeypatch, *, fail=False, probe_fail=False):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "uv":
            prefix = Path(os.environ["TEST_RESET_TOOLS"]) / "amplifier-unified"
            prefix.mkdir()
            (prefix / "fresh").write_text("new install")
            if fail:
                raise subprocess.CalledProcessError(1, args)
        elif len(args) > 3 and "stage_reset_configurations" in str(args[3]):
            from amplifier_web.recovery import stage_reset_configurations
            assert args[0] == Path(os.environ["TEST_RESET_TOOLS"]) / "amplifier-unified/bin/python"
            assert args[1:3] == ("-I", "-c")
            stage_reset_configurations(args[4], json.loads(args[5]), args[6])
        return SimpleNamespace(returncode=0)
    def probe(*args):
        if probe_fail:
            raise ValueError("candidate import failed")
    monkeypatch.setattr(reset, "command", run)
    monkeypatch.setattr(reset, "verify", probe)
    return calls


def test_success_retires_only_owned_caches_and_update_fences(repair_tree, monkeypatch):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    assert (tool / "amplifier-unified/fresh").is_file()
    assert not (tool / "amplifier-unified/old").exists()
    # uv treats every directory here as a tool environment. Recovery copies
    # and reset's lock must not become bogus installed tools.
    assert sorted(path.name for path in tool.iterdir()) == ["amplifier-unified"]
    saved = Path(json.loads((backup / "manifest.json").read_text())["toolBackup"])
    assert not saved.is_relative_to(tool)
    assert (saved / "old").read_text() == "locally edited install"
    for name in reset.REGENERABLE:
        assert not (home / name).exists()
        assert (backup / name / "broken").is_file()
    for name in ("config", "attachments", "unknown-plugin", "sessions"):
        assert (home / name / "keep").read_bytes() == b"private user data\x00"
    with sqlite3.connect(home / "app.sqlite3") as db:
        assert json.loads(db.execute("SELECT value FROM state").fetchone()[0]) == {"sessions": [{"id": "chat", "text": "preserve"}]}
        assert db.execute("SELECT * FROM state_records").fetchall() == [('session', 'chat', 'original bytes')]
    with sqlite3.connect(backup / "app.sqlite3") as db:
        assert "updates" in json.loads(db.execute("SELECT value FROM state").fetchone()[0])


@pytest.mark.parametrize("failure", ["install", "probe", "database"])
def test_failure_restores_install_caches_launcher_and_data(repair_tree, monkeypatch, failure):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    calls = fake_install(monkeypatch, fail=failure == "install", probe_fail=failure == "probe")
    if failure == "database":
        def bad_db(*args):
            raise sqlite3.DatabaseError("corrupt database")
        monkeypatch.setattr(reset, "reset_update_state", bad_db)
    with pytest.raises((subprocess.CalledProcessError, ValueError, sqlite3.DatabaseError)):
        reset.repair(home, "uv", tool, bins, "trusted.whl", {}, (["stop"], ["start"], None), False)
    assert (tool / "amplifier-unified/old").read_text() == "locally edited install"
    assert not (tool / "amplifier-unified/fresh").exists()
    assert sorted(path.name for path in tool.iterdir()) == ["amplifier-unified"]
    assert (bins / "amplifier-unified").is_symlink()
    for name in reset.REGENERABLE:
        assert (home / name / "broken").exists()
    assert calls[0] == ("stop",)
    assert calls[-1] == ("start",)
    with sqlite3.connect(home / "app.sqlite3") as db:
        assert "updates" in json.loads(db.execute("SELECT value FROM state").fetchone()[0])


def test_cache_symlinks_retire_links_without_touching_targets(repair_tree, monkeypatch, tmp_path):
    home, tool, bins = repair_tree
    (home / "foundation/broken").unlink()
    (home / "foundation").rmdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_text("untouched")
    (home / "foundation").symlink_to(external)
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    assert (backup / "foundation").is_symlink()
    assert (external / "keep").read_text() == "untouched"


def test_dependency_free_cli_ignores_corrupt_generation(tmp_path):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates/application-active.json").write_text("not json")
    code = "import sys; sys.path.insert(0, sys.argv.pop(1)); from amplifier_web.cli import main; main()"
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(Path(__file__).parents[1]),
                             "reset", "--data-dir", str(tmp_path), "--dry-run"],
                            env={**os.environ, "HOME": str(tmp_path / "user")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Your chats, settings, sign-ins, and files will be kept." in result.stdout
    assert not (tmp_path / "reset-backups").exists()


def test_existing_host_lease_blocks_reset(tmp_path):
    with reset.repair_lock(tmp_path, shared=True):
        with pytest.raises(RuntimeError, match="already using"):
            with reset.repair_lock(tmp_path):
                pytest.fail("reset acquired an active host's lease")


def test_noninteractive_requires_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "service_commands", lambda home: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = argparse.Namespace(data_dir=str(tmp_path), source=None, yes=False, dry_run=False, no_start=True)
    with pytest.raises(SystemExit, match="--yes"):
        reset.run(args)
    assert not list(tmp_path.iterdir())


def test_service_exact_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    home = tmp_path / "data"
    unit = tmp_path / ".config/systemd/user/amplifier-unified.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"# Managed by amplifier-unified; do not edit.\nExecStart=/bin/amplifier-unified --data-dir '{home}' serve\n")
    assert reset.service_commands(home)[0] == ["systemctl", "--user", "stop", reset.UNIT]
    with pytest.raises(ValueError, match="service uses"):
        reset.service_commands(tmp_path / "dat")


def test_environment_bypasses_project_injection_not_approved_package_policy(monkeypatch):
    for key in ("PYTHONPATH", "UV_CONSTRAINT", "UV_OVERRIDE", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "bad")
    monkeypatch.delenv("UV_NO_CONFIG", raising=False)
    preserved = {
        "UV_INDEX_URL": "https://synthetic-feed.invalid/simple",
        "UV_DEFAULT_INDEX": "https://synthetic-default.invalid/simple",
        "UV_CONFIG_FILE": "/approved/uv.toml", "UV_INDEX_CORPORATE_PASSWORD": "synthetic-secret",
        "UV_KEYRING_PROVIDER": "subprocess", "UV_NATIVE_TLS": "true",
        "HTTPS_PROXY": "https://synthetic-proxy.invalid", "SSL_CERT_FILE": "/approved/ca.pem",
        "PYTHON_KEYRING_BACKEND": "synthetic.backend",
    }
    for key, value in preserved.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("UV_TOOL_DIR", "/owned/tools")
    env = reset.environment()
    assert "UV_NO_CONFIG" not in env and env["UV_NO_CACHE"] == "1"
    assert {key: env[key] for key in preserved} == preserved
    assert env["UV_TOOL_DIR"] == "/owned/tools"
    assert "PYTHONPATH" not in env and "UV_CONSTRAINT" not in env
    assert "UV_OVERRIDE" not in env and "UV_PROJECT_ENVIRONMENT" not in env and "VIRTUAL_ENV" not in env
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    assert reset.environment()["UV_NO_CONFIG"] == "1"  # deliberate operator policy
    assert "UV_NO_CACHE" not in reset.package_environment({})

def test_feed_auth_failure_does_not_retry_with_different_github_credentials():
    env = {"GH_TOKEN": "synthetic-token", "UV_INDEX_CORPORATE_PASSWORD": "synthetic-password"}
    argv = ["uv", "tool", "install", "git+https://github.com/microsoft/amplifier-unified"]
    assert reset.anonymous_retry_environment(argv, env, stderr=
        b"Failed to fetch https://synthetic-feed.invalid/simple/pkg: 401 Unauthorized") is None
    assert env["GH_TOKEN"] == "synthetic-token"


def test_cli_feed_failure_reports_requirement_without_private_installer_output(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "service_commands", lambda home: None)
    def fail(*args):
        raise subprocess.CalledProcessError(1, ["uv", "tool", "install"],
            stderr="Because there is no version of agent-client-protocol==1.0.0\nhttps://credentialuser:secret@synthetic-feed.invalid/simple")
    monkeypatch.setattr(reset, "perform", fail)
    args = argparse.Namespace(data_dir=str(tmp_path), source=None, yes=True, dry_run=False, no_start=True)
    with pytest.raises(SystemExit) as caught:
        reset.run(args)
    assert "agent-client-protocol ==1.0.0" in str(caught.value)
    assert "configured package indexes" in str(caught.value)
    assert all(value not in str(caught.value) for value in ("secret", "credentialuser", "synthetic-feed.invalid"))


def test_running_old_worker_prevents_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "command", lambda *a, **kw: SimpleNamespace(stdout=f"123 {tmp_path}/runtime/abc/.venv/bin/python worker.py\n"))
    with pytest.raises(RuntimeError, match="still closing"):
        reset.assert_no_processes(tmp_path / "tools/amplifier-unified", tmp_path)


def wheel(folder, name, files, requires=()):
    path = folder / f"{name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
        info = f"{name}-1.0.0.dist-info"
        archive.writestr(info + "/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n" + "".join(f"Requires-Dist: {dep}\n" for dep in requires))
        archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: reset-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        if name == "amplifier_unified":
            archive.writestr(info + "/entry_points.txt", "[console_scripts]\namplifier-unified = amplifier_web.cli:main\n")
        archive.writestr(info + "/RECORD", "")
    return path


@pytest.mark.skipif(not os.environ.get("AMPLIFIER_RESET_TEST_UV"), reason="explicit DTU-only uv installation test")
def test_real_uv_repairs_modified_packages_and_missing_dependencies(tmp_path):
    """Actual launcher/self replacement with uv, using synthetic local wheels."""
    uv = os.environ["AMPLIFIER_RESET_TEST_UV"]
    package = Path(reset.__file__).parent
    dep = wheel(tmp_path, "reset_fixture_dependency", {"reset_fixture_dependency/__init__.py": "VALUE = 'fresh'\n"})
    app = wheel(tmp_path, "amplifier_unified", {
        "amplifier_web/__init__.py": "__version__ = '1.0.0'\n",
        "amplifier_web/cli.py": (package / "cli.py").read_bytes(),
        "amplifier_web/reset.py": package.joinpath("reset.py").read_bytes(),
        "amplifier_web/reset_retention.py": package.joinpath("reset_retention.py").read_bytes(),
        "amplifier_web/host_dependencies.py": package.joinpath("host_dependencies.py").read_bytes(),
        "amplifier_web/__main__.py": "from .cli import main; main()\n",
        "amplifier_web/application_generations.py": "def delegate(): pass\n",
        "amplifier_web/browser_entry.py": "def is_windows(): return False\n",
        "amplifier_web/deployment.py": "def load_server_config(*a, **kw): return {}\n",
        "amplifier_web/deployment_service.py": "# synthetic service module\n",
        "amplifier_web/host/__init__.py": "",
        "amplifier_web/host/config.py": "from pathlib import Path\ndef app_home(): return Path.home() / '.amplifier-unified'\n",
        "amplifier_web/installation_health.py": "import json\ndef doctor(home): return {'ok': True}\nasync def status(home, wait=0): return {'ok': False, 'service': 'stopped'}\ndef display(result, **kw): print(json.dumps(result))\n",
        "amplifier_web/app_updates.py": "PROBE = \"import reset_fixture_dependency; assert reset_fixture_dependency.VALUE == 'fresh'\"\n",
    }, [f"reset-fixture-dependency @ {dep.as_uri()}"])
    # uv tool deliberately ignores virtualenv interpreters while resolving 3.13.
    # Keep the already-running standalone Python discoverable after HOME changes.
    interpreter_bin = str(Path(sys._base_executable).resolve().parent)
    env = {**os.environ, "HOME": str(tmp_path), "UV_TOOL_DIR": str(tmp_path / "tools"),
           "UV_TOOL_BIN_DIR": str(tmp_path / "bin"), "PATH": os.pathsep.join((str(Path(uv).parent), interpreter_bin, os.environ["PATH"])),
           "AMPLIFIER_WEB_HOME": str(tmp_path / "data")}
    for key in ("AMPLIFIER_SOURCE_STORE", "PYTHONPATH"):
        env.pop(key, None)
    installed = subprocess.run([uv, "tool", "install", "--no-cache", "--python", "3.13", str(app)], env=env, capture_output=True, text=True)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    prefix = tmp_path / "tools/amplifier-unified"
    sites = next(prefix.glob("lib/python*/site-packages"))
    (sites / "amplifier_web/app_updates.py").write_text("raise RuntimeError('locally hacked app')\n")
    # Emergency reset must not import the damaged ordinary startup helper.
    (sites / "amplifier_web/host_dependencies.py").unlink()
    (sites / "reset_fixture_dependency/__init__.py").unlink()
    (sites / "rogue.py").write_text("untracked installation file")
    data = tmp_path / "data"
    (data / "updates").mkdir(parents=True)
    (data / "updates/application-active.json").write_text("broken receipt")
    (data / "keep").write_text("history sentinel")
    result = subprocess.run([str(tmp_path / "bin/amplifier-unified"), "reset", "--yes", "--no-start", "--source", str(app)], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unified is ready." not in result.stdout
    assert "left stopped" in result.stdout
    assert "Prepared " not in result.stdout and "Installed 1 executable" not in result.stdout
    reports = list((data / "reset-reports").glob('*.log'))
    assert len(reports) == 1
    log = reports[0].read_text()
    assert "doctor --json" in log and "service status --json --wait 0" in log
    assert reports[0].stat().st_mode & 0o777 == 0o600
    assert (data / "keep").read_text() == "history sentinel"
    assert not (sites / "rogue.py").exists()
    assert not (data / "updates").exists()
    listed = subprocess.run([uv, "tool", "list"], env=env, capture_output=True, text=True, check=True)
    assert "amplifier-unified" in listed.stdout
    assert not listed.stderr, listed.stderr
    assert not (tmp_path / "tools/.reset.lock").exists()
    subprocess.run([str(prefix / "bin/python"), "-I", "-c", "import reset_fixture_dependency; assert reset_fixture_dependency.VALUE == 'fresh'"], env=env, check=True)


def test_latest_release_resolves_annotated_tag_to_immutable_commit(monkeypatch):
    commit = "a" * 40
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return SimpleNamespace(stdout=json.dumps({"tag_name": "v1.2.3", "draft": False, "prerelease": False}))
        return SimpleNamespace(stdout=f"{'b' * 40}\trefs/tags/v1.2.3\n{commit}\trefs/tags/v1.2.3^{{}}\n")
    monkeypatch.setattr(reset, "command", run)
    monkeypatch.setattr(reset, "release_metadata", lambda env: {"tag_name": "v1.2.3"})
    assert reset.release_source({}) == f"git+https://github.com/{reset.REPOSITORY}@{commit}"
    assert calls[0][:2] == ("git", "ls-remote")


def test_launchd_service_uses_exact_owner_and_generated_marker(monkeypatch, tmp_path):
    import plistlib
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    path = tmp_path / "Library/LaunchAgents" / f"{reset.LABEL}.plist"
    path.parent.mkdir(parents=True)
    data = {"Label": reset.LABEL, "AmplifierUnifiedMarker": "amplifier-unified launch agent v1",
            "ProgramArguments": ["/owned/bin/amplifier-unified", "--data-dir", str(tmp_path / "data"), "serve"]}
    path.write_bytes(plistlib.dumps(data))
    stop, start, loaded = reset.service_commands(tmp_path / "data")
    assert stop[1] == "bootout" and start[1] == "bootstrap" and loaded[1] == "print"
    data.pop("AmplifierUnifiedMarker")
    path.write_bytes(plistlib.dumps(data))
    with pytest.raises(ValueError, match="unmanaged"):
        reset.service_commands(tmp_path / "data")


def test_subprocess_timeout_terminates_installer_children(tmp_path):
    import time
    marker = tmp_path / "unexpected-write"
    child = f"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1); open({str(marker)!r}, 'w').write('late')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired):
        reset.command(sys.executable, "-c", parent, timeout=.2, capture=True)
    time.sleep(1.2)
    assert not marker.exists()


def test_reset_data_directory_before_and_after_subcommand(monkeypatch):
    from amplifier_web.cli import _parse
    for args in (["--data-dir", "/custom", "reset", "--dry-run"], ["reset", "--data-dir", "/custom", "--dry-run"]):
        monkeypatch.setattr(sys, "argv", ["amplifier-unified", *args])
        assert _parse().data_dir == "/custom"


@pytest.mark.parametrize('failed_check', ['doctor', 'service status'])
def test_failed_post_repair_check_keeps_fresh_install_but_never_claims_ready(repair_tree, monkeypatch, capsys, failed_check):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    fake_install(monkeypatch)
    results = {'doctor': {'ok': True}, 'service status': {'ok': True}}
    results[failed_check] = {'ok': False}
    monkeypatch.setattr(reset, 'post_checks', lambda *a, **kw: results)
    with pytest.raises(RuntimeError):
        reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, (['stop'], ['start'], None), False)
    assert (tool / 'amplifier-unified/fresh').exists()
    assert not (tool / 'amplifier-unified/old').exists()
    assert 'Unified is ready' not in capsys.readouterr().out


def test_no_start_runs_both_checks_without_claiming_ready(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    calls = fake_install(monkeypatch)
    waits = []
    def checks(*a, **kw):
        waits.append(kw['wait'])
        return {'doctor': {'ok': True}, 'service status': {'ok': False}}
    monkeypatch.setattr(reset, 'post_checks', checks)
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, (['stop'], ['start'], None), True)
    assert waits == [0] and ('start',) not in calls
    output = capsys.readouterr().out
    assert 'left stopped' in output and 'Unified is ready' not in output


def test_repair_installs_missing_service_and_checks_readiness(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    calls = fake_install(monkeypatch)
    waits = []
    def checks(*a, **kw):
        waits.append(kw['wait'])
        return {'doctor': {'ok': True}, 'service status': {'ok': True}}
    monkeypatch.setattr(reset, 'post_checks', checks)
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, None, False)
    assert any(args[-2:] == ('service', 'install') for args in calls)
    assert waits == [60]
    assert 'Unified is ready on this computer.' in capsys.readouterr().out


def test_repair_distinguishes_local_readiness_from_public_address(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    fake_install(monkeypatch)
    monkeypatch.setattr(reset, 'post_checks', lambda *a, **kw: {
        'doctor': {'ok': True},
        'service status': {'ok': True, 'url': 'https://127.0.0.1:8941',
                           'publicUrls': ['https://unreachable-onboarding.invalid'],
                           'publicUrlsVerified': False}})
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, None, False)
    output = capsys.readouterr().out
    assert 'Unified is ready on this computer.' in output
    assert 'Host listener: https://127.0.0.1:8941' in output
    assert 'Configured public address (not checked): https://unreachable-onboarding.invalid' in output
    assert 'Open:' not in output


@pytest.mark.parametrize('code', [403, 429, 503])
@pytest.mark.parametrize('access', ['auto', 'anonymous'])
def test_release_lookup_uses_public_link_when_api_unavailable(monkeypatch, code, access):
    import urllib.error
    from contextlib import contextmanager
    calls = []
    @contextmanager
    def urlopen(request, **kwargs):
        calls.append((request.full_url, request.get_method(), dict(request.header_items())))
        if 'api.github.com' in request.full_url:
            raise urllib.error.HTTPError(request.full_url, code, 'Unavailable', {}, None)
        yield SimpleNamespace(geturl=lambda: f'https://github.com/{reset.REPOSITORY}/releases/tag/v1.2.3')
    monkeypatch.setattr(reset.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(reset, 'command', lambda *a, **k: pytest.fail('Must not ask gh for sign-in'))
    assert reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': access})['tag_name'] == 'v1.2.3'
    assert len(calls) == 2 and calls[1][1] == 'HEAD'
    assert all('Authorization' not in headers for _, _, headers in calls)


@pytest.mark.parametrize('url', [
    'https://github.com/other/repo/releases/tag/v1.2.3',
    'http://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3',
    'https://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3-rc1',
    'https://github.com/microsoft/amplifier-unified/releases/tag/main',
    'https://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3?next=bad',
    'https://github.com/login',
])
def test_release_link_requires_canonical_stable_release(url):
    with pytest.raises(ValueError, match='supported release'):
        reset.release_link_metadata(url)


def test_release_failure_explains_rate_limit_and_tls_without_private_details(monkeypatch):
    import urllib.error
    import ssl
    def fail_api(*args, **kwargs):
        raise urllib.error.HTTPError('https://user:secret@proxy.invalid', 403, 'private detail',
                                     {'X-RateLimit-Remaining': '0'}, None)
    def fail_link(*args, **kwargs):
        raise urllib.error.URLError(ssl.SSLCertVerificationError('private certificate detail'))
    monkeypatch.setattr(reset.urllib.request, 'urlopen', fail_api)
    monkeypatch.setattr(reset, 'public_release_link', fail_link)
    with pytest.raises(RuntimeError) as raised:
        reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': 'anonymous'})
    message = str(raised.value)
    assert 'rate limit' in message and 'TLS certificate verification failed' in message
    assert 'secret' not in message and 'private' not in message and 'proxy.invalid' not in message


@pytest.fixture
def legacy_saved_plan():
    """Real local Git provenance; no download or provider use."""
    def create(home, layout="updates/releases/8bfeb10c85fc46af957de342f093a689/foundation/cache/bundle"):
        root = home / layout
        module = root / "modules/fixture"
        module.mkdir(parents=True)
        code = module / "fixture.py"
        code.write_text("VALUE = 1\n")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args],
                                           text=True, stderr=subprocess.DEVNULL).strip()
        git("init", "-b", "main")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", "reset fixture")
        url = "https://example.invalid/legacy-reset"
        git("remote", "add", "origin", url)
        revision = git("rev-parse", "HEAD")
        metadata = root / ".amplifier_cache_meta.json"
        metadata.write_text(json.dumps({"git_url": url, "ref": "main", "commit": revision}))
        external = home.parent / "explicit-local"
        plan = {
            "session": {"orchestrator": {"module": "loop-live", "source": str(module)},
                        "context": {"module": "context-fixture", "source": module.as_uri()}},
            "tools": [{"module": "tool-fixture", "source": str(module), "enabled": False,
                       "config": {"limit": 7, "path": str(external)}},
                      # Incident shape: module-owned regenerable storage, not
                      # another executable source chosen by the reset owner.
                      {"module": "tool-skills", "source": str(module),
                       "config": {"cache_dir": str(root.parent / "skills")}}],
            "providers": [{"module": "provider-fixture", "id": "account",
                           "source": "git+https://example.invalid/pinned@v1",
                           "config": {"api_key": "synthetic-private-value"}}],
            "agents": {"child": {"agents": {"deep": {"hooks": [
                {"module": "hook-fixture", "source": str(module)}]}}}},
            "spawn": {"tools": [{"module": "tool-spawn", "source": str(module)}]},
        }
        directory = home / "sessions/legacy"
        directory.mkdir(parents=True)
        before = (json.dumps(plan, indent=1) + "\n").encode()
        paths = [directory / name for name in ("configuration.json", "effective-configuration.json")]
        for path in paths:
            path.write_bytes(before)
        return SimpleNamespace(root=root, module=module, code=code, metadata=metadata,
                               paths=paths, plan=plan, before=before, external=external,
                               portable=f"git+{url}@{revision}#subdirectory=modules/fixture")
    return create


@pytest.mark.parametrize("layout", [
    "foundation/cache/bundle", "updates/releases/old/foundation/cache/bundle",
    "source-store/objects/repository/revision",
])
def test_reset_preparation_converts_only_retiring_module_declarations(tmp_path, legacy_saved_plan, layout):
    from amplifier_web.recovery import prepare_reset_configurations
    from amplifier_web.host.components import declarations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home, layout)
    # Irrelevant damaged caches are precisely what reset is meant to discard.
    unrelated = home / "foundation/cache/unreferenced"
    unrelated.mkdir(parents=True, exist_ok=True)
    (unrelated / ".amplifier_cache_meta.json").write_text("damaged")
    changes = prepare_reset_configurations(home, reset.REGENERABLE)
    assert {path for path, _, _ in changes} == set(saved.paths)
    for path, before, after in changes:
        assert before == saved.before == path.read_bytes()
        plan = json.loads(after)
        assert [row["source"] for row in declarations(plan)] == [
            saved.portable if row["source"].startswith((str(home), "file:"))
            else row["source"] for row in declarations(saved.plan)]
        assert plan["tools"][0]["enabled"] is False
        assert plan["tools"][0]["config"]["path"] == str(saved.external)
        assert plan["providers"] == saved.plan["providers"]
    assert saved.module.exists()


def test_reset_preserves_skills_cache_and_opaque_paths_without_treating_them_as_sources(
        tmp_path, legacy_saved_plan):
    from amplifier_web.host.components import declarations
    from amplifier_web.recovery import PARTS, prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    cache = saved.plan["tools"][1]["config"]["cache_dir"]
    assert cache == str(home / "updates/releases/8bfeb10c85fc46af957de342f093a689/foundation/cache/skills")
    assert not Path(cache).exists()
    opaque = {"cache_dir": cache, "log_path": str(saved.root / "logs"),
              "nested": {"module": "not-a-declaration", "source": str(saved.root / "missing")}}
    saved.plan["tools"][1]["config"] = opaque
    saved.plan["instruction"] = str(saved.root / "prompt-looking-path")
    saved.plan["agents"]["child"]["instruction"] = saved.plan["instruction"]
    for path in saved.paths:
        path.write_text(json.dumps(saved.plan))
    changes = prepare_reset_configurations(home, PARTS["cache"])
    assert len(changes) == 2
    for path, before, after in changes:
        plan = json.loads(after)
        assert path.read_bytes() == before
        assert plan["tools"][1] == {"module": "tool-skills", "source": saved.portable, "config": opaque}
        assert plan["instruction"] == saved.plan["instruction"]
        assert plan["agents"]["child"]["instruction"] == saved.plan["instruction"]
        # With only opaque retiring paths remaining, there is no conversion.
        path.write_bytes(after)
    assert prepare_reset_configurations(home, PARTS["cache"]) == []
    assert all(not row.get("source", "").startswith(str(home))
               for row in declarations(json.loads(saved.paths[0].read_text())))


@pytest.mark.parametrize("location", ["tool", "session", "spawn", "deep-agent"])
def test_reset_refuses_missing_declared_source_despite_opaque_cache_values(
        tmp_path, legacy_saved_plan, location):
    from amplifier_web.recovery import PARTS, prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    row = {"tool": saved.plan["tools"][0],
           "session": saved.plan["session"]["context"],
           "spawn": saved.plan["spawn"]["tools"][0],
           "deep-agent": saved.plan["agents"]["child"]["agents"]["deep"]["hooks"][0]}[location]
    row["source"] = str(saved.root / "missing-module")
    saved.paths[0].write_text(json.dumps(saved.plan))
    before = [path.read_bytes() for path in saved.paths]
    with pytest.raises(ValueError, match="Reset cannot preserve"):
        prepare_reset_configurations(home, PARTS["cache"])
    assert [path.read_bytes() for path in saved.paths] == before
    assert saved.module.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO admission')
def test_reset_configuration_fifo_is_rejected_without_waiting_for_writer(tmp_path):
    from amplifier_web.recovery import _read_reset_configuration
    home = tmp_path / "app"
    path = home / "sessions/chat/configuration.json"
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    # A subprocess deadline makes a regression fail instead of wedging reset.
    result = subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; from amplifier_web.recovery import _read_reset_configuration; "
         "import sys; _read_reset_configuration(Path(sys.argv[1]), Path(sys.argv[2]))",
         str(path), str(home)], capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "private regular file" in result.stderr


@pytest.mark.parametrize("problem", ["missing", "metadata", "dirty", "context", "child-context"])
def test_reset_preparation_refuses_unreconstructable_references_before_any_write(tmp_path, legacy_saved_plan, problem):
    from amplifier_web.recovery import prepare_reset_configurations
    saved = legacy_saved_plan(tmp_path / "app")
    if problem == "missing":
        saved.root.rename(tmp_path / "removed-source")
    elif problem == "metadata":
        saved.metadata.write_text("{}")
    elif problem == "dirty":
        saved.code.write_text("VALUE = 2\n")
    else:
        node = (saved.plan["agents"]["child"]["agents"]["deep"]
                if problem == "child-context" else saved.plan)
        node["context"] = {"fixture:policy": str(saved.module / "policy.md")}
        saved.paths[1].write_text(json.dumps(saved.plan))
    originals = [path.read_bytes() for path in saved.paths]
    with pytest.raises(ValueError, match="Reset cannot preserve"):
        prepare_reset_configurations(tmp_path / "app", reset.REGENERABLE)
    assert [path.read_bytes() for path in saved.paths] == originals
    assert saved.module.exists() is (problem != "missing")


def test_reset_preparation_preserves_external_and_nonretiring_source_paths(tmp_path, legacy_saved_plan):
    from amplifier_web.recovery import PARTS, prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    sources = [str(saved.external), saved.external.as_uri(),
               "file:" + str(saved.external),
               str(home / "source-store/objects/unretired/missing"),
               "git+https://example.invalid/pinned@" + "a" * 40]
    saved.plan["tools"].extend({"module": f"tool-external-{i}", "source": source}
                              for i, source in enumerate(sources))
    for path in saved.paths:
        path.write_text(json.dumps(saved.plan))
    changes = prepare_reset_configurations(home, PARTS["cache"])
    for _, _, after in changes:
        plan = json.loads(after)
        assert plan["tools"][0]["source"] == saved.portable
        assert [row["source"] for row in plan["tools"][2:]] == sources


def test_reset_preparation_refuses_linked_plan_reads(tmp_path, legacy_saved_plan):
    from amplifier_web.recovery import prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    path = saved.paths[0]
    external = tmp_path / "external-plan.json"
    path.rename(external)
    path.symlink_to(external)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        prepare_reset_configurations(home, reset.REGENERABLE)
    assert path.read_bytes() == before
    assert saved.module.exists()


@pytest.mark.parametrize("convert", [False, True])
def test_cli_reset_accepts_large_saved_plans_without_a_reset_size_limit(
        repair_tree, monkeypatch, legacy_saved_plan, convert):
    from amplifier_web.host.components import declarations
    from amplifier_web.recovery import prepare_reset_configurations
    home, tool, bins = repair_tree
    saved = legacy_saved_plan(home)
    saved.plan["instruction"] = "x" * (2 * 1024 * 1024)
    if not convert:
        for i, row in enumerate(declarations(saved.plan)):
            row["source"] = saved.portable if i % 2 else str(saved.external)
    before = (json.dumps(saved.plan, indent=1) + "\n").encode()
    for path in saved.paths:
        path.write_bytes(before)
    changes = prepare_reset_configurations(home, reset.REGENERABLE)
    assert len(changes) == (2 if convert else 0)
    assert all(path.read_bytes() == before for path in saved.paths)
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    for path in saved.paths:
        plan = json.loads(path.read_text())
        assert plan["instruction"] == saved.plan["instruction"]
        if convert:
            assert plan["tools"][0]["source"] == saved.portable
            assert (backup / path.relative_to(home)).read_bytes() == before
        else:
            assert path.read_bytes() == before
    assert not saved.module.exists()


@pytest.mark.parametrize("deny_file_acl", [False, True])
def test_windows_reset_reader_uses_file_size_and_retains_private_handle_checks(
        tmp_path, monkeypatch, deny_file_acl):
    from contextlib import contextmanager
    from amplifier_web import recovery, windows_files
    home = tmp_path / "app"
    path = home / "sessions/large/configuration.json"
    content = json.dumps({"instruction": "x" * (2 * 1024 * 1024)}).encode()
    calls = []

    class API:
        def info(self, handle, target, *, directory):
            assert (handle, target, directory) in {
                ("parent", path.parent, True), ("file", path, False)}
            return SimpleNamespace(volume=1, index_high=0, index_low=2,
                                   size_high=0, size_low=len(content))

        def open(self, target):
            assert target == path
            calls.append("open")
            return "file"

        def validate_acl(self, handle, *, private):
            assert private is True
            calls.append("acl:" + handle)
            if handle == "file" and deny_file_acl:
                raise PermissionError("fixture private ACL denied")

        def read(self, handle, *, limit):
            assert handle == "file" and limit == len(content)
            assert "acl:parent" in calls and "acl:file" in calls
            calls.append("read")
            return content

        def close(self, handle):
            assert handle == "file"
            calls.append("close")

    # Keep the real _check/_existing methods: only the OS boundary is a fixture.
    pinned = object.__new__(windows_files.PinnedDirectory)
    pinned.path, pinned._api = path.parent, API()
    pinned._pins = [(path.parent, "parent", (1, 0, 2), True)]

    @contextmanager
    def directory(parent, *, owned_root):
        assert parent == path.parent and owned_root == home
        yield pinned

    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(recovery, "os", SimpleNamespace(name="nt"))
    if deny_file_acl:
        with pytest.raises(PermissionError, match="private ACL denied"):
            recovery._read_reset_configuration(path, home)
        assert "read" not in calls
    else:
        assert recovery._read_reset_configuration(path, home) == content
    assert calls[-1] == "close"


def test_reset_preserves_pending_configuration_and_sealed_request_identity(tmp_path, legacy_saved_plan):
    from amplifier_web.recovery import prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    # Host operation owner and native session IDs need not be identical.
    owner = home / "sessions/owner"
    owner.mkdir()
    pending = owner / "pending-configuration.json"
    pending.write_bytes(saved.before)
    receipt = owner / "configuration-operations/sealed.json"
    receipt.parent.mkdir()
    receipt.write_bytes(b'{"requestHash":"untouched","effect":"saved_not_mounted"}\n')
    before = receipt.read_bytes()
    with pytest.raises(ValueError, match="pending configuration"):
        prepare_reset_configurations(home, reset.REGENERABLE)
    assert pending.read_bytes() == saved.before
    assert receipt.read_bytes() == before
    assert all(path.read_bytes() == saved.before for path in saved.paths)
    assert saved.module.exists()


@pytest.mark.parametrize("phase", ["ready", "cancelled"])
def test_reset_does_not_treat_retained_finished_requests_as_pending(tmp_path, legacy_saved_plan, phase):
    from amplifier_web.recovery import PARTS, prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    # Management.command and finish_saved_cancellation unlink the queue file;
    # the immutable request remains in its operation record for history.
    receipt = home / "sessions/owner/configuration-operations/finished.json"
    receipt.parent.mkdir(parents=True)
    original = json.dumps({"state": {"phase": phase}, "request": {"config": saved.plan}}).encode()
    receipt.write_bytes(original)
    assert len(prepare_reset_configurations(home, PARTS["cache"])) == 2
    assert receipt.read_bytes() == original


def test_reset_pending_opaque_cache_path_is_not_an_executable_dependency(tmp_path, legacy_saved_plan):
    from amplifier_web.host.components import declarations
    from amplifier_web.recovery import PARTS, prepare_reset_configurations
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    for row in declarations(saved.plan):
        row["source"] = saved.portable
    pending = home / "sessions/owner/pending-configuration.json"
    pending.parent.mkdir()
    original = json.dumps(saved.plan).encode()
    pending.write_bytes(original)
    receipt = pending.parent / "configuration-operations/unknown.json"
    receipt.parent.mkdir()
    receipt.write_bytes(b'{"state":{"phase":"error","effect":"unknown"}}\n')
    retained = receipt.read_bytes()
    assert len(prepare_reset_configurations(home, PARTS["cache"])) == 2
    assert pending.read_bytes() == original
    assert receipt.read_bytes() == retained


@pytest.mark.parametrize("failure", [None, "conversion", "replace", "database"])
def test_cli_reset_converts_legacy_plans_and_rolls_back_exact_bytes(repair_tree, monkeypatch, legacy_saved_plan, failure):
    home, tool, bins = repair_tree
    saved = legacy_saved_plan(home)
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    calls = fake_install(monkeypatch)
    if failure == "conversion":
        saved.metadata.write_text("{}")
    elif failure == "replace":
        replace = Path.replace
        def fail_replace(path, target):
            if path.name == "effective-configuration.json" and "portable-configurations" in path.parts:
                raise OSError("fixture replacement failure")
            return replace(path, target)
        monkeypatch.setattr(Path, "replace", fail_replace)
    elif failure == "database":
        def fail_database(*args):
            raise sqlite3.DatabaseError("fixture database failure")
        monkeypatch.setattr(reset, "reset_update_state", fail_database)
    if failure:
        with pytest.raises((ValueError, OSError, sqlite3.DatabaseError)):
            reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
        assert all(path.read_bytes() == saved.before for path in saved.paths)
        assert saved.module.exists()
        assert (tool / "amplifier-unified/old").exists()
    else:
        backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
        for path in saved.paths:
            plan = json.loads(path.read_text())
            assert plan["tools"][0]["source"] == saved.portable
            assert plan["tools"][1]["config"] == saved.plan["tools"][1]["config"]
            assert (backup / path.relative_to(home)).read_bytes() == saved.before
        assert not saved.module.exists()
    assert any("stage_reset_configurations" in str(args) for args in calls)


def test_cli_reset_preserves_a_plan_changed_after_preparation(repair_tree, monkeypatch, legacy_saved_plan):
    home, tool, bins = repair_tree
    saved = legacy_saved_plan(home)
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    command = reset.command
    concurrent = b'{"tools":[],"instruction":"keep the newer edit"}\n'
    def change_after_preparation(*args, **kwargs):
        result = command(*args, **kwargs)
        if len(args) > 3 and "stage_reset_configurations" in str(args[3]):
            saved.paths[1].write_bytes(concurrent)
        return result
    monkeypatch.setattr(reset, "command", change_after_preparation)
    with pytest.raises(ValueError, match="changed during reset"):
        reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    assert saved.paths[0].read_bytes() == saved.before
    assert saved.paths[1].read_bytes() == concurrent
    assert saved.module.exists()


def test_cli_conversion_failure_does_not_suggest_network_repair(tmp_path, monkeypatch, legacy_saved_plan):
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(reset, "command", fail)
    moves = []
    with pytest.raises(RuntimeError, match="No cache was retired"):
        reset.prepare_saved_configurations(tmp_path / "fresh", home, home / "reset-backups/id", {}, moves)
    assert moves == []
    assert all(path.read_bytes() == saved.before for path in saved.paths)
    assert saved.module.exists()


@pytest.mark.skipif(not os.environ.get("AMPLIFIER_RESET_TEST_PREFIX"),
                    reason="explicit isolated installed-package reset bridge")
def test_cli_conversion_uses_fresh_installed_interpreter(tmp_path, legacy_saved_plan):
    prefix = Path(os.environ["AMPLIFIER_RESET_TEST_PREFIX"])
    home = tmp_path / "app"
    saved = legacy_saved_plan(home)
    backup = home / "reset-backups/fixture"
    backup.mkdir(parents=True)
    moves = []
    # No command/provenance mocks: -I must import the installed candidate.
    reset.prepare_saved_configurations(prefix, home, backup, reset.environment(), moves)
    assert len(moves) == 2
    for path in saved.paths:
        assert json.loads(path.read_bytes())["tools"][0]["source"] == saved.portable
        assert (backup / path.relative_to(home)).read_bytes() == saved.before
    assert saved.module.exists()  # Conversion does not perform cache retirement.
