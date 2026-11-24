"""Finite local-Git control for the pinned-provider fixture's shallow donor.

No uv, app preparation, provider repository or external transport. The actual
maintained fixture shim rewrites a synthetic public-looking URL to a fresh local
mirror. Git's terminal status and refs are observed, not inferred from intent.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_partial_update_native import _transport


@pytest.mark.parametrize("shallow", [True, False], ids=["shallow-donor", "complete-donor"])
def test_local_provider_donor_controls_branch_fetch_and_clone_guard(tmp_path, record_property, shallow):
    root = tmp_path / "synthetic-provider"
    root.mkdir(mode=0o700)
    home, templates, binpath = (root / name for name in ("home", "templates", "bin"))
    for path in (home, templates, binpath):
        path.mkdir(mode=0o700)
    git = Path("/usr/bin/git")
    assert git.is_file()
    env = {
        "PATH": str(binpath) + os.pathsep + "/usr/bin:/bin", "HOME": str(home),
        "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TEMPLATE_DIR": str(templates),
        "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
        "GIT_AUTHOR_NAME": "Synthetic fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    effects = []

    def run(cwd, *args, executable=git, require=True):
        result = subprocess.run([str(executable), *map(str, args)], cwd=cwd,
                                env=env, capture_output=True, timeout=15)
        # All commands operate on new synthetic repositories. Keep a small fixed
        # result record, not machine paths or raw argv, in exported JUnit.
        effects.append({"verb": str(args[0]), "exit": result.returncode})
        if require:
            assert result.returncode == 0, (args[0], result.returncode, result.stderr)
        return result

    version = run(root, "--version").stdout.decode().strip()
    full = root / "complete"
    run(root, "init", "--initial-branch=main", full)
    for value in ("one", "two"):
        (full / "value.txt").write_text(value)
        run(full, "add", "value.txt")
        run(full, "commit", "-m", value)
    tip = run(full, "rev-parse", "HEAD").stdout.strip().decode()
    donor = root / "donor"
    clone_args = ("--depth=1",) if shallow else ()
    run(root, "clone", *clone_args, full.as_uri(), donor)
    assert run(donor, "rev-parse", "--is-shallow-repository").stdout.strip() == (
        b"true" if shallow else b"false")
    donor_shallow_before = (donor / ".git/shallow").read_bytes() if shallow else None

    # Same local path clone --bare shape used by the real pinned-provider test.
    mirror = root / "mirror.git"
    run(root, "clone", "--bare", donor, mirror)
    assert run(mirror, "rev-parse", "refs/heads/main").stdout.strip().decode() == tip
    assert run(mirror, "rev-parse", "--is-shallow-repository").stdout.strip() == (
        b"true" if shallow else b"false")
    transport = root / "transport.json"
    url = "https://pinned-fixture.invalid/provider"
    transport.write_text(json.dumps({url: mirror.as_uri()}))
    _transport(binpath, transport, root / "absent-fault.json", root / "unused-app")
    database = root / "uv-shaped-database"
    run(root, "init", database)
    refspec = "+refs/heads/main:refs/remotes/origin/main"
    fetched = run(database, "fetch", "--force", "--update-head-ok", url, refspec,
                  executable=binpath / "git", require=False)
    object_result = run(database, "cat-file", "-e", tip + "^{commit}", require=False)
    branch = run(database, "rev-parse", "origin/main^0", require=False)
    fallback = run(database, "rev-parse", "refs/remotes/origin/tags/main^0", require=False)
    fetch_head = (database / ".git/FETCH_HEAD").read_bytes()
    guarded = run(root, "clone", "--bare", "--reject-shallow", donor,
                  root / "guarded.git", require=False)
    facts = {
        "donor": "shallow" if shallow else "complete", "gitVersion": version,
        "fetchExit": fetched.returncode, "commitObjectExit": object_result.returncode,
        "branchPeelExit": branch.returncode, "tagFallbackExit": fallback.returncode,
        "shallowRefWarning": b"shallow roots are not allowed to be updated" in fetched.stderr,
        "fetchHeadEmpty": not fetch_head, "cloneGuardExit": guarded.returncode,
        "branchEqualsTip": branch.stdout.strip().decode() == tip,
    }
    record_property("synthetic_git_fetch", json.dumps(facts, sort_keys=True))
    assert fetched.returncode == 0
    assert object_result.returncode == 0
    assert fallback.returncode != 0
    if shallow:
        assert facts["shallowRefWarning"]
        assert branch.returncode != 0 and not fetch_head
        assert guarded.returncode != 0
        assert (donor / ".git/shallow").read_bytes() == donor_shallow_before
    else:
        assert not facts["shallowRefWarning"]
        assert branch.returncode == 0 and facts["branchEqualsTip"]
        assert tip.encode() in fetch_head and guarded.returncode == 0
        # Ordinary branch advance is still visible; an explicit original commit
        # continues to identify its old content, without changing the source ref.
        (full / "value.txt").write_text("three")
        run(full, "add", "value.txt")
        run(full, "commit", "-m", "three")
        moved = run(full, "rev-parse", "HEAD").stdout.strip().decode()
        run(full, "push", mirror, "main:main")
        run(database, "fetch", "--force", "--update-head-ok", url, refspec, executable=binpath / "git")
        assert run(database, "rev-parse", "origin/main^0").stdout.strip().decode() == moved
        assert run(database, "show", tip + ":value.txt").stdout == b"two"
        assert run(database, "show", moved + ":value.txt").stdout == b"three"
    # The same shim must continue refusing an address with no local mapping.
    rejected = run(database, "fetch", "https://unmapped-fixture.invalid/repo", "main",
                   executable=binpath / "git", require=False)
    assert rejected.returncode != 0 and b"not allowed" in rejected.stderr
    record_property("synthetic_git_terminal_commands", json.dumps(effects, sort_keys=True))
