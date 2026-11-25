"""The offer requires two qualified, unredirected caches and the entire original contract."""
import json
import os

import pytest

from amplifier_web.context_continuity import digest
from amplifier_web.skill_cache_move import PACKAGE, REPOSITORY, SUBDIRECTORY, verified_move
from test_approved_skill_cache_recovery import NEW_GEN, OLD_GEN, qualified_registry


@pytest.mark.parametrize("change", [
    "unchanged", "missing-generation", "dirty-package", "different-tracked-package",
    "different-package-revision", "cache-symlink", "linked-lock", "hardlinked-lock",
    "writable-cache", "nonempty-lock", "different-lock", "different-account", "explicit-current-cache",
])
def test_historical_cache_and_package_proof(tmp_path, change):
    old = qualified_registry(tmp_path, OLD_GEN)
    new = qualified_registry(tmp_path, NEW_GEN)
    source = f"git+{REPOSITORY}@main#subdirectory={SUBDIRECTORY}"
    def contract(registry):
        return {"plan": {"tools": [{"module": "tool-skills", "source": source,
                    "config": {"cache_dir": str(registry / 'cache/skills')}}]}, "selectionBinding": "account"}
    before = digest(contract(old))
    current = contract(new)
    slots = (("tools", 0),)
    cache = old / "cache/skills"
    for root in [cache, new / "cache/skills"]:
        (root / ".a.lock").touch(mode=0o600)
    if change == "missing-generation":
        (old.parent / "validated.json").unlink()
    elif change in {"dirty-package", "different-tracked-package", "different-package-revision"}:
        receipt = old.parent / "runtime-installed.json"
        rows = json.loads(receipt.read_text())
        row = next(row for row in rows if row["name"] == PACKAGE)["cacheSource"]
        if change == "dirty-package":
            row["dirty"] = True
        elif change == "different-tracked-package":
            row["trackedContentSha256"] = "e" * 64
        else:
            row["revision"] = "e" * 40
        receipt.write_text(json.dumps(rows))
    elif change == "cache-symlink":
        cache.rename(cache.with_name("prior"))
        cache.symlink_to(cache.with_name("prior"), target_is_directory=True)
    elif change == "linked-lock":
        (cache / ".a.lock").unlink()
        (cache / ".a.lock").symlink_to(new / "cache/skills/.a.lock")
    elif change == "hardlinked-lock":
        os.link(cache / ".a.lock", cache / ".b.lock")
    elif change == "writable-cache":
        cache.chmod(0o777)
    elif change == "nonempty-lock":
        (cache / ".a.lock").write_bytes(b"changed")
    elif change == "different-lock":
        (cache / ".a.lock").rename(cache / ".b.lock")
    elif change == "different-account":
        current["selectionBinding"] = "another-account"
    elif change == "explicit-current-cache":
        slots = ()
    proof = verified_move(tmp_path, new, slots, current, before, OLD_GEN)
    assert bool(proof) == (change == "unchanged")
    if proof:
        assert proof["oldContractSha256"] == before and proof["newContractSha256"] == digest(current)
