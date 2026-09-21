"""Standalone stdlib installer tail embedded in a downloaded setup script."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def write(path, contents, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


COMMAND_MARKER = '# Amplifier Terminal managed command v1'
COMMAND_SOURCE = """
import json, os, re, sys
from pathlib import Path
try:
    root = Path(sys.argv[1])
    identity = json.loads((root / 'default.json').read_text())['id']
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError()
    launch = root / 'connections' / identity / 'launch'
    if not launch.is_file() or not os.access(launch, os.X_OK):
        raise ValueError()
except (OSError, ValueError, KeyError, TypeError):
    print('Terminal setup is incomplete. Finish setup from your Unified service before launching.', file=sys.stderr)
    raise SystemExit(1)
os.execv(str(launch), [str(launch), *sys.argv[2:]])
"""


def create_terminal_command(root, environment):
    """Provide an emulator-independent command without editing the user's shell."""
    command = root / 'launch'
    if command.exists() and COMMAND_MARKER not in command.read_text().splitlines()[:2]:
        raise ValueError('The terminal command location is already in use.')
    # Use the managed base interpreter, not the candidate venv: a failed install
    # removes its candidate, while the command must still open the old default.
    python = (environment / 'bin/python').resolve(strict=True)
    write(command, '#!/bin/sh\n' + COMMAND_MARKER + '\nexec ' +
          shlex.join([str(python), '-I', '-c', COMMAND_SOURCE, str(root)]) + ' "$@"\n', 0o700)
    directory = Path.home() / '.local/bin' if root == Path.home() / '.local/share/amplifier-terminal' else root / 'bin'
    alias = directory / 'amplifier-terminal'
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        alias.symlink_to(command)
    except FileExistsError:
        if not alias.is_symlink() or alias.readlink() != command:
            return command, None
    except OSError:
        return command, None
    return command, alias


def create_shortcut(root, connection, server, identity):
    """Launch directly in Terminal, without typing into an interactive login shell."""
    label = re.sub('[^A-Za-z0-9.-]', '-', urllib.parse.urlsplit(server).hostname or '')[:60] or 'Unified'
    if sys.platform == 'darwin':
        directory = Path.home() / 'Applications' if root == Path.home() / '.local/share/amplifier-terminal' else root
        shortcut = directory / f'Amplifier Terminal - {label} - {identity[:8]}.terminal'
        # RunCommandAsShell means use CommandString as the shell itself. No
        # login shell, startup prompts or input injection precede the client.
        contents = plistlib.dumps({
            'type': 'Window Settings', 'name': f'Amplifier Terminal - {label} - {identity[:8]}',
            'CommandString': shlex.join(['/bin/sh', str(connection / 'launch')]),
            'RunCommandAsShell': True, 'shellExitAction': 1,
        }).decode()
        mode = 0o600
    else:
        shortcut = root / f'Amplifier-Terminal-{label}-{identity[:8]}.command'
        contents = '#!/bin/sh\nexec ' + shlex.quote(str(connection / 'launch')) + ' "$@"\n'
        mode = 0o700
    if shortcut.exists():
        raise ValueError('A launcher already exists at the new connection location.')
    write(shortcut, contents, mode)
    return shortcut


def install(profile, root, environment):
    server = profile['server']
    parsed = urllib.parse.urlsplit(server)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('Invalid service address')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
        raise ValueError('A remote service requires verified HTTPS')
    if profile['expiresAt'] <= time.time():
        raise ValueError('Setup expired. Download a new setup file.')
    from importlib.metadata import version
    from amplifier_tui.launcher import executable
    expected = profile.get('clientVersion')
    if (not isinstance(expected, str) or not expected
            or version('amplifier-app-tui') != expected or not os.access(executable(None), os.X_OK)):
        raise ValueError('Installed terminal client failed validation')
    context = ssl.create_default_context(cadata=profile['ca'] or None)
    data = json.dumps({'grant': profile['grant']}).encode()
    request = urllib.request.Request(server + '/api/terminal/redeem', data=data,
                                     headers={'Content-Type': 'application/json'})
    # Reject redirects: an enrollment secret or device token must never be
    # forwarded to a different origin or login page.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError('Setup expired or was already used. Download a new setup file.') from None
        raise ValueError('The service could not connect this terminal. Download a new setup file and retry.') from None
    identity, token = result['id'], result['token']
    if not re.fullmatch('[a-f0-9]{32}', identity) or not re.fullmatch(r'amt_[a-f0-9]{32}\.[A-Za-z0-9_-]{64}', token):
        raise ValueError('The service returned an invalid connection credential')
    connection = root / 'connections' / identity
    write(connection / 'token', token + '\n')
    write(connection / 'ca.crt', profile['ca'])
    options = ['--server', server, '--token-file', str(connection / 'token')]
    if profile['ca']:
        options += ['--ca-file', str(connection / 'ca.crt')]
    python = environment / 'bin/python'
    # argv forwards only view/workspace choices; credential/server overrides
    # belong to a separate explicit connection, never this saved launcher.
    launcher = '''#!/bin/sh
for argument in "$@"; do
  case "$argument" in
    --session|--session=*|--resume|--resume=*|--workspace|--workspace=*|--client|--client=*|--state-dir|--state-dir=*|--new|--list-sessions|--version|--help|-h) ;;
    -*) echo 'This launcher accepts conversation options only. Prepare another connection to change servers.' >&2; exit 2;;
  esac
done
unset AMPLIFIER_UNIFIED_TOKEN AMPLIFIER_UNIFIED_URL
exec ''' + shlex.join([str(python), '-m', 'amplifier_tui.connected', *options]) + ' "$@"\n'
    write(connection / 'launch', launcher, 0o700)
    record = {'id': identity, 'server': server, 'name': profile['name'],
              'environment': str(environment), 'tokenFile': str(connection / 'token'),
              'caFile': str(connection / 'ca.crt') if profile['ca'] else None}
    write(connection / 'connection.json', json.dumps(record))
    command, alias = create_terminal_command(root, environment)
    try:
        shortcut = create_shortcut(root, connection, server, identity)
    except (OSError, ValueError):
        shortcut = None
    with (root / '.selection.lock').open('a') as lock:
        os.chmod(root / '.selection.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        write(environment / '.activated', identity + '\n')
        write(root / 'default.json', json.dumps({'id': identity}))
    print('Connected to ' + server)
    found = shutil.which('amplifier-terminal')
    on_path = alias and found and Path(found).resolve() == command.resolve()
    print('Run in this terminal: ' + ('amplifier-terminal' if on_path else shlex.quote(str(alias or command))))
    print('You can use the same command in WezTerm, iTerm2, Terminal, or another terminal app.')
    if not alias:
        print('An existing amplifier-terminal command was preserved. Use the full command above.')
    if shortcut:
        print('Optional window launcher: ' + str(shortcut))
    else:
        print('The optional window launcher was not created. Use the terminal command above.')
    print('Remove this connection from the service setup page to revoke its access.')
    return record


def main():
    try:
        profile = json.loads(Path(sys.argv[1]).read_text())
        install(profile, Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).resolve())
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        # HTTP errors and file paths can include private information. Keep the
        # install failure helpful without printing credentials or response bodies.
        print('Connection setup failed. Check connectivity and download a fresh setup file. An unused connection can be removed on the setup page.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
