import os

import pytest

from amplifier_web.deployment import config_path, load_server_config, save_server_config, validate_origin


def test_server_config_is_private_atomic_and_defaults_to_loopback(tmp_path):
    config = load_server_config(tmp_path)
    assert config["bind"] == ["127.0.0.1"]
    assert config_path(tmp_path).stat().st_mode & 0o777 == 0o600


def test_public_origin_is_exact_and_remote_bind_requires_https_tls(tmp_path):
    assert validate_origin("https://host.example:8941") == "https://host.example:8941"
    assert validate_origin("https://BÜCHER.example:443") == "https://xn--bcher-kva.example"
    assert validate_origin("http://HOST.example:80") == "http://host.example"
    assert validate_origin("https://[2001:0DB8:0:0::1]:443") == "https://[2001:db8::1]"
    with pytest.raises(ValueError):
        validate_origin("https://host.example/path")
    with pytest.raises(ValueError, match="invalid port"):
        validate_origin("https://host.example:0")
    with pytest.raises(ValueError, match="IDNA"):
        validate_origin("https://bad_host.example")
    with pytest.raises(ValueError, match="Non-loopback"):
        save_server_config(tmp_path, {"schema_version": 1, "bind": ["192.0.2.1"], "port": 8941,
                                     "public_origins": ["https://host.example"], "session_ttl_seconds": 60,
                                     "tls": {"method": "none", "cert": "", "key": ""}})


@pytest.mark.parametrize(("key", "value"), [("port", True), ("session_ttl_seconds", False)])
def test_server_config_rejects_boolean_numbers(tmp_path, key, value):
    config = load_server_config(tmp_path)
    with pytest.raises(ValueError, match=key):
        save_server_config(tmp_path, {**config, key: value})


def test_tls_sans_use_canonical_public_origin_hostname(tmp_path):
    from cryptography import x509
    from amplifier_web.tls import ca_bytes, setup_local_ca

    config = load_server_config(tmp_path)
    setup_local_ca(tmp_path, {**config, "public_origins": ["https://BÜCHER.example:443"]})
    leaf = x509.load_pem_x509_certificate((tmp_path / "config/tls/leaf.crt").read_bytes())
    names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "xn--bcher-kva.example" in names.get_values_for_type(x509.DNSName)
    assert ca_bytes(tmp_path)