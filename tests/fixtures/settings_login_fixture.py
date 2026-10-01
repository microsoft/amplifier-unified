"""Synthetic OAuth process for browser acceptance; never contacts a provider."""
import json,sys,time
from pathlib import Path
request=json.loads(sys.stdin.readline())
plan=request.get('authMode')=='chatgpt_plan'
instructions=(['Open https://auth.openai.com/api/accounts/authorize?state=fixture&code_challenge=fixture'] if plan else
 ['Open this URL on any device: https://auth.openai.com/codex/device','Enter code: TEST-1234'])
for instruction in instructions:
 print(json.dumps({'status':'waiting','instruction':instruction}),flush=True)
time.sleep(5)
path=Path(request['tokenFile']);path.parent.mkdir(parents=True,exist_ok=True)
tokens={'access_token':'fixture-private-access','refresh_token':'fixture-private-refresh'}
if plan:tokens.update(auth_mode='chatgpt_plan',subject='fixture-subject',client_id='fixture-client',email='fixture@example.test',scopes=['chatgpt.tokens.use.direct'])
path.write_text(json.dumps(tokens));path.chmod(0o600)
print(json.dumps({'status':'completed'}),flush=True)
