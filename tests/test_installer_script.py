"""Exercise the installer with synthetic downloads and isolated user files."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/install.sh'

@pytest.mark.parametrize('helper', ['path', 'new', 'standard', 'legacy'])
@pytest.mark.parametrize('anonymous', [False, True])
@pytest.mark.parametrize('verbose,bad_checksum',[(False,False),(True,False),(False,True)])
def test_installer_checks_release_keeps_cli_and_uses_canonical_launcher(tmp_path,verbose,bad_checksum,anonymous,helper):
    home=tmp_path/'user';home.mkdir()
    cli=home/'.amplifier'/'settings.yaml';cli.parent.mkdir();cli.write_text('working-cli: keep-me\n')
    bin=tmp_path/'bin';bin.mkdir()
    downloads=tmp_path/'downloads';downloads.mkdir()
    wheel='amplifier_unified-0.20.101-py3-none-any.whl'
    (downloads/wheel).write_bytes(b'synthetic wheel')
    digest='0'*64 if bad_checksum else hashlib.sha256(b'synthetic wheel').hexdigest()
    (downloads/'SHA256SUMS').write_text(digest+'  '+wheel+'\n')
    def executable(name,text):
        path=bin/name;path.write_text('#!/bin/bash\nset -eu\n'+text);path.chmod(0o755)
    executable('id','echo 1000\n')
    executable('curl','if [ \"$1\" = --version ]; then exit 0; fi\nurl=""; dest=""\nwhile [ "$#" -gt 0 ]; do case "$1" in https:*) url="$1"; shift;; -o) dest="$2"; shift 2;; *) shift;; esac; done\nif [[ "$url" = */latest ]]; then echo -n https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.101; else cp "$FIXTURE_DOWNLOADS/${url##*/}" "$dest"; fi\n')
    executable('uv','if [ "${FIXTURE_ANONYMOUS:-}" = 1 ]; then test "$GIT_CONFIG_GLOBAL" = /dev/null; test "${GH_TOKEN:-}" = ""; test "$GIT_CONFIG_COUNT" = 4; fi\ncase "$1 ${2:-}" in\n "--version ") echo uv-fixture;;\n "python install") echo verbose-python-setup;;\n "run --no-project") shift 5; exec "$FIXTURE_PYTHON" "$@";;\n "tool install") echo installed >> "$FIXTURE_EVENTS";;\n "tool dir") echo "$FIXTURE_BIN";;\n *) exit 3;;\nesac\n')
    executable('git','exit 0\n')
    executable('amplifier-unified','command -v uv >/dev/null\nuv --version >/dev/null\ncommand -v amplifier-unified >/dev/null\necho "launcher $*" >> "$FIXTURE_EVENTS"\necho "Open: http://127.0.0.1:18941"\n')
    if helper != 'path':
        target = downloads/'uv' if helper == 'new' else home/('.cargo/bin/uv' if helper == 'legacy' else '.local/bin/uv')
        target.parent.mkdir(parents=True,exist_ok=True)
        (bin/'uv').rename(target)
    (downloads/'install.sh').write_text('mkdir -p "$UV_INSTALL_DIR"\ncp "$FIXTURE_DOWNLOADS/uv" "$UV_INSTALL_DIR/uv"\n')
    events=tmp_path/'events'
    env={**os.environ,'HOME':str(home),'PATH':str(bin)+':/usr/bin:/bin','TMPDIR':str(tmp_path),
         'FIXTURE_ANONYMOUS':'1' if anonymous else '0', 'GH_TOKEN':'synthetic-token', 'FIXTURE_DOWNLOADS':str(downloads),'FIXTURE_PYTHON':sys.executable,'FIXTURE_BIN':str(bin),'FIXTURE_EVENTS':str(events)}
    result=subprocess.run(['bash',str(SCRIPT),'--no-open',*(['--verbose'] if verbose else []), *(['--anonymous'] if anonymous else [])],env=env,text=True,capture_output=True)
    assert cli.read_text()=='working-cli: keep-me\n'
    if bad_checksum:
        assert result.returncode!=0
        assert not events.exists()
        assert 'Installation stopped' in result.stderr
    else:
        assert result.returncode==0,result.stderr
        assert events.read_text()=='installed\nlauncher --no-open\n'
        assert 'Open: http://127.0.0.1:18941' in result.stdout
        assert ('verbose-python-setup' in result.stdout)==verbose
    assert not list(tmp_path.glob('amplifier-install.*'))


@pytest.mark.parametrize('platform', ['Linux', 'Darwin'])
@pytest.mark.parametrize('missing', [('curl',), ('git',), ('curl', 'git')])
def test_prerequisites_are_grouped_and_platform_specific(tmp_path, platform, missing):
    bin = tmp_path / 'bin'; bin.mkdir()
    # A restricted PATH proves absence rather than relying on the test host.
    for name in ('uname', 'id', 'curl', 'git', 'apt-get'):
        if name in missing:
            continue
        body = {'uname': f'echo {platform}', 'id': 'echo 1000'}.get(name, 'exit 0')
        tool = bin / name; tool.write_text('#!/bin/sh\n' + body + '\n'); tool.chmod(0o755)
    result = subprocess.run(['/bin/bash', str(SCRIPT)], text=True, capture_output=True,
                            env={**os.environ, 'HOME': str(tmp_path), 'PATH': str(bin)})
    assert result.returncode == 1
    assert 'needs: ' + ' '.join(missing) in result.stderr
    assert ('xcode-select' in result.stderr) == (platform == 'Darwin')
    assert ('apt-get install -y curl git ca-certificates' in result.stderr) == (platform == 'Linux')
    assert 'normal user, without sudo' in result.stderr
