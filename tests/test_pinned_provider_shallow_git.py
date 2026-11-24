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

    def check(condition, reason):
        if not condition:
            pytest.fail(reason, pytrace=False)

    def run(cwd, *args, executable=git, require=True):
        problem = None
        try:
            result = subprocess.run([str(executable), *map(str, args)], cwd=cwd,
                                    env=env, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            problem = "Synthetic Git command timed out"
        except OSError:
            problem = "Synthetic Git command could not start"
        if problem is not None:
            pytest.fail(problem, pytrace=False)
        # All commands operate on new synthetic repositories. Keep a small fixed
        # result record, not machine paths or raw argv, in exported JUnit.
        effects.append({"verb": str(args[0]), "exit": result.returncode})
        if require and result.returncode != 0:
            pytest.fail(f"Synthetic Git {args[0]} exited {result.returncode}", pytrace=False)
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
    check(run(donor, "rev-parse", "--is-shallow-repository").stdout.strip() == (
        b"true" if shallow else b"false"), "Donor shallow status mismatch")
    donor_shallow_before = (donor / ".git/shallow").read_bytes() if shallow else None

    # Same local path clone --bare shape used by the real pinned-provider test.
    mirror = root / "mirror.git"
    run(root, "clone", "--bare", donor, mirror)
    check(run(mirror, "rev-parse", "refs/heads/main").stdout.strip().decode() == tip,
          "Mirror tip mismatch")
    check(run(mirror, "rev-parse", "--is-shallow-repository").stdout.strip() == (
        b"true" if shallow else b"false"), "Bare clone shallow status mismatch")
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
        "fetchHeadContainsTip": tip.encode() in fetch_head,
    }
    record_property("synthetic_git_fetch", json.dumps(facts, sort_keys=True))
    check(fetched.returncode == 0, "Fetch terminal exit must be zero")
    check(object_result.returncode == 0, "Fetched commit object is missing")
    check(fallback.returncode != 0, "Unexpected tag fallback success")
    if shallow:
        check(facts["shallowRefWarning"], "Missing shallow-ref warning")
        check(branch.returncode != 0 and facts["fetchHeadEmpty"], "Shallow ref refusal not reproduced")
        check(guarded.returncode != 0, "Shallow donor was not rejected")
        check((donor / ".git/shallow").read_bytes() == donor_shallow_before,
              "Original shallow boundary changed")
    else:
        check(not facts["shallowRefWarning"], "Complete donor produced shallow warning")
        check(branch.returncode == 0 and facts["branchEqualsTip"], "Complete donor branch failed")
        check(facts["fetchHeadContainsTip"] and guarded.returncode == 0, "Complete donor guard/fetch failed")
        # Ordinary branch advance is still visible; an explicit original commit
        # continues to identify its old content, without changing the source ref.
        (full / "value.txt").write_text("three")
        run(full, "add", "value.txt")
        run(full, "commit", "-m", "three")
        moved = run(full, "rev-parse", "HEAD").stdout.strip().decode()
        run(full, "push", mirror, "main:main")
        run(database, "fetch", "--force", "--update-head-ok", url, refspec, executable=binpath / "git")
        check(run(database, "rev-parse", "origin/main^0").stdout.strip().decode() == moved,
              "Moving branch did not advance")
        check(run(database, "show", tip + ":value.txt").stdout == b"two", "Original commit content changed")
        check(run(database, "show", moved + ":value.txt").stdout == b"three", "New branch content mismatch")
    # The same shim must continue refusing an address with no local mapping.
    rejected = run(database, "fetch", "https://unmapped-fixture.invalid/repo", "main",
                   executable=binpath / "git", require=False)
    unmapped_rejected = rejected.returncode != 0 and b"not allowed" in rejected.stderr
    check(unmapped_rejected, "Unmapped transport was not refused")
    record_property("synthetic_git_terminal_commands", json.dumps(effects, sort_keys=True))
