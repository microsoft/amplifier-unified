"""Optional launcher stays separate from service startup and host workspace policy."""
import sys
from pathlib import Path
from types import ModuleType

from amplifier_web import cli, terminal_cli
from amplifier_web.deployment import DEFAULT_SERVER, save_server_config


def invoke(monkeypatch, home, *arguments):
    # Exercise the optional-client route without reading a personal managed install.
    monkeypatch.setattr(terminal_cli, 'saved', lambda: None)
    calls = []
    module = ModuleType('amplifier_tui.connected')
    module.main = calls.append
    monkeypatch.setitem(sys.modules, 'amplifier_tui.connected', module)
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--data-dir', str(home), 'tui', *arguments])
    cli.main()
    return calls[0]


def test_local_launcher_uses_configured_port_and_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv('AMPLIFIER_UNIFIED_URL', raising=False)
    monkeypatch.delenv('AMPLIFIER_UNIFIED_TOKEN', raising=False)
    save_server_config(tmp_path, {**DEFAULT_SERVER, 'port': 9321})
    args = invoke(monkeypatch, tmp_path, '--session', 'saved', '--client', 'terminal-a')
    assert args == ['--token-file', str(tmp_path / 'config/auth/control-token'),
                    '--server', 'http://127.0.0.1:9321', '--client', 'terminal-a', '--session', 'saved',
                    '--workspace', str(Path.cwd().resolve())]


def test_remote_launcher_never_attaches_local_credentials(monkeypatch, tmp_path):
    args = invoke(monkeypatch, tmp_path, '--server', 'https://host.example', '--new',
                  '--workspace', '/host/work', '--ca-file', 'host.crt')
    assert args == ['--server', 'https://host.example', '--ca-file', 'host.crt',
                    '--workspace', '/host/work', '--new']
    assert '--token-file' not in args
