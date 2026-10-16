"""Real CLI configuration writes; no listener may start with incomplete TLS."""
import ssl
import sys

import pytest
from aiohttp import web

from amplifier_web import cli
from amplifier_web.deployment import config_path, load_server_config, save_server_config
from amplifier_web.tls import setup_local_ca, ssl_context, tls_ready


def configure(monkeypatch, home, key, value):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "config", "set", key, value])
    cli.main()


def refuse_serve(monkeypatch, home, message):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(home), "--workspace", str(home), "--no-open", "serve"])
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a, **k: None)
    monkeypatch.setattr("amplifier_web.host.config.load_config", lambda *a, **k: None)
    monkeypatch.setattr(web, "run_app", lambda *a, **k: pytest.fail("Incomplete TLS reached listener startup"))
    with pytest.raises(ValueError, match=message):
        cli.main()


@pytest.mark.parametrize("first", ["cert", "key"])
def test_cli_tls_paths_can_be_configured_in_either_order_without_remote_bind(tmp_path, monkeypatch, first):
    # Generate actual local material in a separate owned directory. The tested
    # host begins unconfigured and follows the reported command sequence.
    material = tmp_path / "material"
    setup_local_ca(material, load_server_config(material))
    paths = {"cert": str(material / "config/tls/leaf.crt"), "key": str(material / "config/tls/leaf.key")}
    home = tmp_path / "host"
    original = load_server_config(home)
    save_server_config(home, {**original, "local_extension": {"retain": ["exact"]}})
    configure(monkeypatch, home, "tls.method", "ca")
    configure(monkeypatch, home, "public_origins", '["https://192.0.2.10:8765"]')
    configure(monkeypatch, home, "port", "8765")
    before = config_path(home).read_bytes()
    with pytest.raises(ValueError, match="Non-loopback"):
        configure(monkeypatch, home, "bind", '["192.0.2.10"]')
    assert config_path(home).read_bytes() == before
    configure(monkeypatch, home, "tls." + first, paths[first])
    staged = load_server_config(home)
    assert staged["bind"] == ["127.0.0.1"]
    assert staged["tls"][first] == paths[first]
    before = config_path(home).read_bytes()
    with pytest.raises(ValueError, match="Non-loopback"):
        configure(monkeypatch, home, "bind", '["192.0.2.10"]')
    assert config_path(home).read_bytes() == before
    refuse_serve(monkeypatch, home, "both tls.cert and tls.key")
    assert config_path(home).read_bytes() == before
    second = "key" if first == "cert" else "cert"
    configure(monkeypatch, home, "tls." + second, paths[second])
    configure(monkeypatch, home, "bind", '["192.0.2.10", "127.0.0.1"]')
    complete = load_server_config(home)
    assert complete["tls"] == {"method": "ca", **paths}
    assert complete["bind"] == ["192.0.2.10", "127.0.0.1"]
    assert complete["local_extension"] == {"retain": ["exact"]}
    assert config_path(home).stat().st_mode & 0o777 == 0o600
    assert isinstance(ssl_context(home, complete), ssl.SSLContext)


@pytest.mark.parametrize("key,value", [("tls.method", "unknown"), ("tls.cert", "server.crt"), ("tls.key", "server.key")])
def test_invalid_or_disabled_tls_edits_do_not_replace_saved_bytes(tmp_path, monkeypatch, key, value):
    original = load_server_config(tmp_path)
    before = config_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="TLS"):
        configure(monkeypatch, tmp_path, key, value)
    assert config_path(tmp_path).read_bytes() == before
    assert load_server_config(tmp_path) == original


def test_enabled_tls_with_no_paths_fails_before_starting_listener(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path, "tls.method", "ca")
    before = config_path(tmp_path).read_bytes()
    refuse_serve(monkeypatch, tmp_path, "both tls.cert and tls.key")
    assert config_path(tmp_path).read_bytes() == before


def test_tls_readiness_reports_incomplete_saved_pair_with_existing_ca(tmp_path):
    complete = setup_local_ca(tmp_path, load_server_config(tmp_path))
    incomplete = save_server_config(tmp_path, {**complete, "tls": {**complete["tls"], "cert": ""}})
    assert not tls_ready(tmp_path, incomplete)


def test_atomic_pair_setting_retains_strict_origin_and_tls_startup_checks(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path, "tls", '{method: ca, cert: missing.crt, key: missing.key}')
    before = config_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Non-loopback"):
        configure(monkeypatch, tmp_path, "bind", '["192.0.2.10"]')
    with pytest.raises(ValueError, match="HTTPS public origins"):
        configure(monkeypatch, tmp_path, "public_origins", '["http://192.0.2.10"]')
    assert config_path(tmp_path).read_bytes() == before
    configure(monkeypatch, tmp_path, "public_origins", '["https://192.0.2.10"]')
    configure(monkeypatch, tmp_path, "bind", '["192.0.2.10"]')
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(tmp_path), "--workspace", str(tmp_path), "--no-open", "serve"])
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a, **k: None)
    monkeypatch.setattr("amplifier_web.host.config.load_config", lambda *a, **k: None)
    monkeypatch.setattr(web, "run_app", lambda *a, **k: pytest.fail("Missing certificate reached listener startup"))
    with pytest.raises(FileNotFoundError):
        cli.main()
