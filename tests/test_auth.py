import hashlib
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

from amplifier_web.auth import _signed, control_token, data_identity, validate_next_path
from amplifier_web.auth import history_scope, new_session, with_history_scope, _verified


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


def test_history_scope_is_stable_opaque_and_signin_owner_root_bound(tmp_path, monkeypatch):
    secret = "synthetic-secret"
    credential = new_session(secret)
    scope = history_scope(secret, credential, tmp_path)
    assert len(scope) == 64
    assert scope == history_scope(secret, credential, tmp_path / ".")
    assert scope != history_scope(secret, new_session(secret), tmp_path)
    assert scope != history_scope("different-secret", credential, tmp_path)
    assert scope != history_scope(secret, credential, tmp_path / "different-root")
    assert not _verified(secret, scope, 60, "session")
    assert secret not in scope and credential not in scope
    monkeypatch.setattr("amplifier_web.auth.os.geteuid", lambda: 12345, raising=False)
    first = history_scope(secret, credential, tmp_path)
    monkeypatch.setattr("amplifier_web.auth.os.geteuid", lambda: 54321)
    assert first != history_scope(secret, credential, tmp_path)


def test_history_scope_decorates_only_request_owned_browser_snapshot():
    source = {"client": {"id": "browser", "kind": "web"}, "revision": 1}
    result = with_history_scope({"history_scope": "opaque"}, source)
    assert result["client"]["historyScope"] == "opaque"
    assert "historyScope" not in source["client"]
    assert with_history_scope({}, source) is source
    terminal = {"client": {"id": "terminal", "kind": "terminal"}}
    assert with_history_scope({"history_scope": "opaque"}, terminal) is terminal