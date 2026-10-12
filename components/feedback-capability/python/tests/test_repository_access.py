import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from amplifier_unified_feedback import github


@pytest.mark.asyncio
async def test_public_read_without_cli_and_authenticated_write_not_retried(monkeypatch):
    original=httpx.AsyncClient;requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'id':12})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setattr(github.shutil,'which',lambda _:None)
    assert await github.github_api('repos/example/public',None)=={'id':12}
    assert 'authorization' not in requests[0].headers
    with pytest.raises(FileNotFoundError):await github.github_api('repos/example/public/issues',{'title':'fixture'})
    with pytest.raises(FileNotFoundError):await github.github_api('user',None)
    assert len(requests)==1


@pytest.mark.asyncio
async def test_explicit_anonymous_never_uses_cli(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda _:httpx.Response(404)),**kwargs))
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS','anonymous')
    def unexpected(_):raise AssertionError('Authenticated fallback must not run')
    monkeypatch.setattr(github.shutil,'which',unexpected)
    with pytest.raises(RuntimeError,match='anonymously'):await github.github_api('repos/example/private',None)


@pytest.mark.asyncio
async def test_private_read_falls_back_and_public_output_remains_bounded(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS',raising=False)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda _:httpx.Response(404)),**kwargs))
    child=AsyncMock();child.returncode=0;child.stdout.read.side_effect=[b'{"id":99}',b''];child.stdin.close=MagicMock()
    create=AsyncMock(return_value=child);monkeypatch.setattr(github.asyncio,'create_subprocess_exec',create)
    monkeypatch.setattr(github.shutil,'which',lambda _: '/fixture/gh')
    assert await github.github_api('repos/example/private',None)=={'id':99}
    assert create.await_count==1 and 'GET' in create.call_args.args
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'x'*4_000_001)),**kwargs))
    with pytest.raises(ValueError,match='bounded'):await github.github_api('repos/example/public',None)
    assert create.await_count==1
