import os
import subprocess
import sys
from amplifier_unified_workspaces import workspace_provisioning as provisioning


def test_download_retry_preserves_pinned_descriptor_and_original_command(tmp_path, monkeypatch):
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS',raising=False)
    monkeypatch.setenv('GH_TOKEN','fixture-token')
    original=subprocess.Popen;attempts=[]
    def spawn(argv,**kwargs):
        attempts.append((argv,dict(kwargs['env']),kwargs['pass_fds']))
        script=('import sys;print("https://github.com/example/public: SAML enforcement",file=sys.stderr);sys.exit(128)' if len(attempts)==1 else 'print("verified-commit")')
        return original([sys.executable,'-c',script],**kwargs)
    monkeypatch.setattr(provisioning.subprocess,'Popen',spawn)
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:assert provisioning._raw_git(fd,'fetch','https://github.com/example/public','main')=='verified-commit'
    finally:os.close(fd)
    assert len(attempts)==2 and attempts[0][0]==attempts[1][0] and attempts[0][2]==attempts[1][2]
    assert attempts[0][1]['GH_TOKEN']=='fixture-token' and 'GH_TOKEN' not in attempts[1][1]
    assert attempts[1][1]['AMPLIFIER_GITHUB_ACCESS']=='anonymous' and os.environ['GH_TOKEN']=='fixture-token'
