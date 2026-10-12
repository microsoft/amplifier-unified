"""Exercise the installer with synthetic downloads and isolated user files."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/install.sh'

@pytest.mark.parametrize('anonymous', [False, True])
@pytest.mark.parametrize('verbose,bad_checksum',[(False,False),(True,False),(False,True)])
def test_installer_checks_release_keeps_cli_and_uses_canonical_launcher(tmp_path,verbose,bad_checksum,anonymous):
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
    executable('curl','url=""; dest=""\nwhile [ "$#" -gt 0 ]; do case "$1" in https:*) url="$1"; shift;; -o) dest="$2"; shift 2;; *) shift;; esac; done\nif [[ "$url" = */latest ]]; then echo -n https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.101; else cp "$FIXTURE_DOWNLOADS/${url##*/}" "$dest"; fi\n')
    executable('uv','if [ "${FIXTURE_ANONYMOUS:-}" = 1 ]; then test "$GIT_CONFIG_GLOBAL" = /dev/null; test "${GH_TOKEN:-}" = ""; test "$GIT_CONFIG_COUNT" = 4; fi\ncase "$1 $2" in\n "python install") echo verbose-python-setup;;\n "run --no-project") shift 5; exec "$FIXTURE_PYTHON" "$@";;\n "tool install") echo installed >> "$FIXTURE_EVENTS";;\n "tool dir") echo "$FIXTURE_BIN";;\n *) exit 3;;\nesac\n')
    executable('amplifier-unified','echo "launcher $*" >> "$FIXTURE_EVENTS"\necho "Open: http://127.0.0.1:18941"\n')
    events=tmp_path/'events'
    env={**os.environ,'HOME':str(home),'PATH':str(bin)+':'+os.environ['PATH'],'TMPDIR':str(tmp_path),
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
