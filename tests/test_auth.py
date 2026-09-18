import hashlib
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

from amplifier_web.auth import _signed, control_token, data_identity, validate_next_path


def test_next_paths_fail_closed():
    assert validate_next_path("/safe?tab=one") == "/safe?tab=one"
    for value in ("//example.test", "https://example.test", "/../secret", "/\\example.test"):
        assert validate_next_path(value) == "/"


def test_signed_sessions_expire_and_reject_modified_values():
    secret = "test-secret"
    valid = _signed(secret, {"kind": "session", "issued": int(time.time()), "nonce": "one"})
    from amplifier_web.auth import _verified
    assert _verified(secret, valid, 60, "session")
    assert not _verified(secret, valid + "x", 60, "session")
    expired = _signed(secret, {"kind": "session", "issued": 1, "nonce": "one"})
    assert not _verified(secret, expired, 60, "session")
    future = _signed(secret, {"kind": "session", "issued": int(time.time()) + 60, "nonce": "one"})
    assert not _verified(secret, future, 60, "session")


def test_concurrent_credential_initialization_converges_on_one_value(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(control_token, [tmp_path] * 4))
    assert len(set(values)) == 1


def test_data_identity_preserves_digest_for_equivalent_paths(tmp_path, monkeypatch):
    home=tmp_path/'home'
    data=home/'state'
    data.mkdir(parents=True)
    alias=home/'state-link'
    alias.symlink_to(data,target_is_directory=True)
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.chdir(home)
    expected=hashlib.sha256(str(data.resolve()).encode()).hexdigest()
    for path in (data,Path('state'),Path('~/state'),alias):
        assert data_identity(path)==expected
    assert data_identity(home/'other-state')!=expected
    assert not (home/'other-state').exists()