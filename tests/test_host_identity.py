import pytest
from amplifier_web.host_identity import local_host_identity, require_local_host


def test_existing_hostname_identity_and_explicit_target(monkeypatch):
    monkeypatch.setattr('amplifier_web.host_identity.socket.gethostname', lambda: 'fixture-host')
    assert local_host_identity() == require_local_host('fixture-host')
    assert local_host_identity() == {'scope':'local', 'id':'fixture-host', 'label':'fixture-host', 'identitySource':'foundation-owner-hostname'}
    for wrong in (None, '', 'other-host', {'id':'fixture-host'}):
        with pytest.raises(ValueError, match='different local host'):
            require_local_host(wrong)
