"""Synthetic OAuth process for browser acceptance; never contacts a provider."""
import json,sys,time
json.loads(sys.stdin.readline())
for instruction in ['Open this URL on any device: https://auth.openai.com/codex/device','Enter code: TEST-1234']:
 print(json.dumps({'status':'waiting','instruction':instruction}),flush=True)
time.sleep(5)
print(json.dumps({'status':'completed'}),flush=True)
