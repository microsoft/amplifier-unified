import json
from amplifier_web.provider_health import generation,record,issue
from amplifier_web.attention import snapshot


def test_auth_failure_is_bound_to_configuration_and_credential_file(tmp_path):
    token=tmp_path/'tokens.json';token.write_text('expired-private-token')
    config={'token_file_path':str(token),'api_key':'private-value'}
    key=generation('provider-openai-chatgpt',config)
    record(tmp_path,'one',key,authentication_required=True)
    assert issue(tmp_path,'one','provider-openai-chatgpt',config)
    assert 'private-value' not in next((tmp_path/'provider-health').glob('*.json')).read_text()
    assert not issue(tmp_path,'one','provider-openai-chatgpt',{**config,'auth_mode':'chatgpt_plan'})
    token.write_text('renewed-private-token')
    assert not issue(tmp_path,'one','provider-openai-chatgpt',config)


def test_attention_trail_remains_actionable_until_account_recovers():
    state={'setup':{'providers':[{'id':'one','enabled':True,'authenticationRequired':True}]}}
    report=snapshot(state); item=report['items'][0]
    assert report['settingsUnread']==1 and report['pages']['ai-connections']==1
    assert item['providerId']=='one'
    state['attentionRead']={item['id']:item['fingerprint']}
    assert snapshot(state)['settingsUnread']==1
    state['setup']['providers'][0]['authenticationRequired']=False
    assert snapshot(state)['settingsUnread']==0
