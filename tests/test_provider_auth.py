"""OAuth worker protocol checks without account access or network calls."""
import io
import json
import os
import sys
import types
import pytest
from amplifier_web import provider_auth

@pytest.mark.asyncio
async def test_plan_auth_worker_passes_candidate_contract_and_only_emits_progress(tmp_path,monkeypatch,capsys):
    path=tmp_path/'candidate.json';seen={}
    async def login(**kwargs):
        seen.update(kwargs)
        kwargs['print_fn']('Open https://auth.openai.com/api/accounts/authorize?state=s')
        tokens={'access_token':'fixture-secret','refresh_token':'fixture-refresh'}
        path.write_text(json.dumps(tokens))
        return tokens
    parent=types.ModuleType('amplifier_module_provider_openai_chatgpt')
    plan=types.ModuleType('amplifier_module_provider_openai_chatgpt.plan_auth');plan.login=login
    monkeypatch.setitem(sys.modules,parent.__name__,parent);monkeypatch.setitem(sys.modules,plan.__name__,plan)
    request={'module':'provider-openai-chatgpt','authMode':'chatgpt_plan','tokenFile':str(path),'sourceTokenFile':'/saved/old.json','registrationFile':'/private/registration.json','hostFile':'/private/host.json','enablePlan':True}
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(request)+'\n'))
    previous=os.umask(0o077)
    try:await provider_auth.main()
    finally:os.umask(previous)
    assert seen['source_token_file_path']=='/saved/old.json'
    assert seen['registration_file_path']=='/private/registration.json'
    assert seen['request_plan_permission'] is True
    assert seen['app_name']=='Amplifier Unified'
    output=capsys.readouterr().out
    assert 'fixture-secret' not in output and 'fixture-refresh' not in output
    assert json.loads(output.splitlines()[-1])=={'status':'completed'}
    assert path.stat().st_mode & 0o777==0o600

@pytest.mark.asyncio
async def test_unavailable_plan_support_fails_without_saving_credentials(tmp_path,monkeypatch,capsys):
    parent=types.ModuleType('amplifier_module_provider_openai_chatgpt')
    monkeypatch.setitem(sys.modules,parent.__name__,parent)
    monkeypatch.delitem(sys.modules,parent.__name__+'.plan_auth',raising=False)
    path=tmp_path/'candidate.json'
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'module':'provider-openai-chatgpt','authMode':'chatgpt_plan','tokenFile':str(path),'hostFile':str(tmp_path/'host.json')})+'\n'))
    previous=os.umask(0o077)
    try:await provider_auth.main()
    finally:os.umask(previous)
    assert json.loads(capsys.readouterr().out)['status']=='failed'
    assert not path.exists()
