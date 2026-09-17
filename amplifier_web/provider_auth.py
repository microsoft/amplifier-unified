"""Isolated provider-owned OAuth login; stdout contains status, never tokens."""
import asyncio
import json
import os
from pathlib import Path
import sys


def emit(**event):
    print(json.dumps(event),flush=True)

async def main():
    request=json.loads(sys.stdin.readline())
    if request.get('module')!='provider-openai-chatgpt':
        emit(status='failed',error='This provider does not expose browser login. Configure its credential fields.');return
    path=Path(request['tokenFile'])
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Restrict every file created by the provider before it saves OAuth tokens.
    os.umask(0o077)
    from amplifier_module_provider_openai_chatgpt.oauth import login
    try:
        tokens=await login(token_file_path=str(path),print_fn=lambda text:emit(status='waiting',instruction=str(text)))
        if not isinstance(tokens,dict) or not tokens.get('access_token'):
            emit(status='failed',error='The provider returned no authenticated session.');return
        path.chmod(0o600)
        emit(status='completed')
    except asyncio.CancelledError:raise
    except Exception as exc:
        # Provider exception strings may include HTTP bodies or credentials.
        emit(status='failed',error='Provider login failed ('+type(exc).__name__+'). Retry the device login.')

if __name__=='__main__':asyncio.run(main())
