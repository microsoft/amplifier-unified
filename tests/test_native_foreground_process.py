import asyncio
import sys

import pytest

from amplifier_web.native_foreground import NativeForeground, observation_metadata


@pytest.mark.parametrize('source',[
    "import sys;sys.stdout.write('x'*900000);sys.stdout.flush()",
    "print('not json')",
    "print('[]')",
    "raise SystemExit(3)",
])
async def test_helper_failures_bounded_and_reaped(monkeypatch,source):
    create=asyncio.create_subprocess_exec;processes=[]
    async def fixture(*args,**kwargs):
        assert args[1]=='-I' and args[-1]=='status'
        p=await create(sys.executable,'-I','-c',source,**kwargs);processes.append(p);return p
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fixture)
    with pytest.raises(ValueError):await NativeForeground().run('status')
    assert processes[0].returncode is not None


async def test_cancellation_kills_native_process_and_does_not_accept_partial_output(monkeypatch):
    create=asyncio.create_subprocess_exec;processes=[];entered=asyncio.Event()
    async def fixture(*args,**kwargs):
        p=await create(sys.executable,'-I','-c',"import time;print('{',flush=True);time.sleep(30)",**kwargs);processes.append(p);entered.set();return p
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fixture)
    task=asyncio.create_task(NativeForeground().run('capture'));await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert processes[0].returncode is not None


async def test_chunked_helper_output_is_read_to_eof(monkeypatch):
    create=asyncio.create_subprocess_exec
    async def fixture(*args,**kwargs):
        return await create(sys.executable,'-I','-c',"import time;print('{',end='',flush=True);time.sleep(.03);print('\"available\":false}')",**kwargs)
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fixture)
    assert await NativeForeground().run('status')=={'available':False}


def test_metadata_does_not_trust_unknown_fields_or_unbounded_values():
    data={'window':{'id':'42','title':'Fixture','application':None,'bounds':[0,0,1,1]},'secret':'omit','captureScope':'arbitrary'}
    assert observation_metadata(data)=={'window':data['window'],'captureScope':'visible foreground window region','reportedBy':'native-host'}
    data['window']['title']='x'*301
    with pytest.raises(ValueError):observation_metadata(data)


async def test_native_hang_is_killed_at_hard_deadline(monkeypatch):
    create=asyncio.create_subprocess_exec;processes=[]
    async def fixture(*args,**kwargs):
        p=await create(sys.executable,'-I','-c','import time;time.sleep(30)',**kwargs);processes.append(p);return p
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fixture)
    with pytest.raises(TimeoutError):await NativeForeground().run('status')
    assert processes[0].returncode is not None
