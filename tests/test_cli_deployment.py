from argparse import Namespace
import sys

import pytest

from amplifier_web import cli
from amplifier_web.cli import _config, _parse, _server_overrides
from amplifier_web.deployment import load_server_config, save_server_config


def test_config_cli_sets_and_resets_server_setting(tmp_path, capsys):
    _config(Namespace(config_command="set", key="session_ttl_seconds", value="120"), tmp_path)
    assert load_server_config(tmp_path)["session_ttl_seconds"] == 120
    _config(Namespace(config_command="get", key="session_ttl_seconds"), tmp_path)
    assert capsys.readouterr().out == "120\n"
    _config(Namespace(config_command="reset", key="session_ttl_seconds"), tmp_path)
    assert load_server_config(tmp_path)["session_ttl_seconds"] == 604800


def test_serve_options_are_top_level_repeatable_and_overlay_nested_tls(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "amplifier-unified", "--bind", "127.0.0.1", "--bind", "192.0.2.5",
        "--public-origin", "https://BÜCHER.example:443", "--tls-cert", "leaf.crt",
        "--tls-key", "leaf.key", "--session-ttl", "120", "serve",
    ])
    args = _parse()
    config = load_server_config(tmp_path, overrides=_server_overrides(args))
    assert args.command == "serve"
    assert config["bind"] == ["127.0.0.1", "192.0.2.5"]
    assert config["public_origins"] == ["https://xn--bcher-kva.example"]
    assert config["tls"] == {"method": "ca", "cert": "leaf.crt", "key": "leaf.key"}
    assert config["session_ttl_seconds"] == 120


def test_host_alias_is_exclusive_with_bind_and_options_do_not_follow_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--host", "localhost", "serve"])
    assert _parse().host == "localhost"
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--host", "localhost", "--bind", "127.0.0.1", "serve"])
    with pytest.raises(SystemExit):
        _parse()
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "serve", "--bind", "127.0.0.1"])
    with pytest.raises(SystemExit):
        _parse()
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "service", "install", "--replace"])
    args = _parse()
    assert args.service_command == "install" and args.replace


def test_tls_option_overlay_preserves_unmentioned_nested_values(tmp_path):
    config = load_server_config(tmp_path)
    save_server_config(tmp_path, {**config, "tls": {"method": "ca", "cert": "old.crt", "key": "old.key"}})
    overlaid = load_server_config(tmp_path, overrides={"tls": {"cert": "new.crt"}})
    assert overlaid["tls"] == {"method": "ca", "cert": "new.crt", "key": "old.key"}


def test_nonloopback_serve_is_rejected_before_socket_binding(tmp_path, monkeypatch):
    args = Namespace(port=None, workspace=str(tmp_path), no_open=True, bind=["192.0.2.5"], host=None,
                     public_origin=None, tls_cert=None, tls_key=None, session_ttl_seconds=None)
    monkeypatch.setattr(cli.web, "run_app", lambda *args, **kwargs: pytest.fail("unsafe configuration bound a socket"))
    with pytest.raises(ValueError, match="Non-loopback"):
        cli._serve(args, tmp_path)