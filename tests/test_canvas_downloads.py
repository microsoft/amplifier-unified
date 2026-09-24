import copy
from urllib.parse import quote

import pytest

from amplifier_web.canvas_downloads import filename
from amplifier_web.service import AppService
from test_canvas_views import app, command, target, view


@pytest.mark.parametrize(('canvas', 'expected'), [
    ({'kind': 'markdown', 'path': '/private/project/Plan été.md'}, 'Plan été.md'),
    ({'kind': 'code', 'path': r'C:\project\run.py'}, 'run.py'),
    ({'kind': 'text', 'path': '/project/.gitignore'}, '.gitignore'),
    ({'kind': 'markdown', 'path': '/project/bad\r\n"name.md'}, 'bad___name.md'),
    ({'kind': 'markdown', 'path': '/project/..'}, 'canvas.md'),
    ({'kind': 'markdown', 'title': 'Not a filename'}, 'canvas.md'),
    ({'kind': 'babylon'}, 'canvas-3d.html'),
    ({'kind': 'image', 'path': '/project/photo.png'}, 'canvas.txt'),
])
def test_safe_source_filename(canvas, expected):
    assert filename(canvas) == expected


async def test_both_download_actions_keep_original_name_and_snapshot(app, tmp_path):
    path = tmp_path / 'Plan été.md'
    original = '# Saved\r\n\nOriginal content.'
    path.write_bytes(original.encode())
    await command(app, 'canvas.show', {'kind': 'auto', 'path': str(path)})
    current = view(app)
    identity = current['resourceId']
    path.unlink()
    with app.clients.bind('one'):
        before = copy.deepcopy(app.state['sessions'])
    for action, args in [
        ('canvas.download', {'id': identity}),
        ('canvas.views.command', {**target(current), 'action': 'canvas.download', 'args': {}}),
    ]:
        result = await command(app, action, args)
        effect = result['effects'][0]
        assert effect['filename'] == path.name
        assert effect['content'] == original
    with app.clients.bind('one'):
        assert app.state['sessions'] == before


async def test_name_survives_restart(tmp_path):
    path = tmp_path / 'saved.md'
    path.write_text('# Saved')
    first = AppService(tmp_path / 'app', workspace=tmp_path)
    await first.dispatch('session.create', {})
    await first.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = first.state['canvas']['id']
    await first.close()
    path.unlink()
    restored = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await restored.dispatch('canvas.select', {'id': identity})
        result = await restored.dispatch('canvas.download', {'id': identity})
        assert result['effects'][0]['filename'] == 'saved.md'
        assert result['effects'][0]['content'] == '# Saved'
    finally:
        await restored.close()


async def test_http_download_name_and_body_survive_source_deletion(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path / 'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    path = tmp_path / 'résumé.html'
    body = '<h1>Original</h1>\r\n'
    path.write_bytes(body.encode())
    await service.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = service.state['canvas']['id']
    path.unlink()
    response = await client.get(f'/api/canvas/{identity}/download')
    assert response.status == 200
    assert response.headers['Content-Disposition'] == "attachment; filename*=UTF-8''" + quote(path.name, safe='')
    assert await response.read() == body.encode()


async def test_exact_download_and_source_urls_do_not_follow_later_revisions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path/'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    content = '<h1>Original</h1><!--' + 'x' * 1_100_000 + '-->'
    path = tmp_path/'large.html'
    path.write_text(content)
    result = await service.dispatch('canvas.show', {'kind':'auto','path':str(path)})
    identity = result['result']['id']
    effects = [(await service.dispatch(action, {'id':identity}))['effects'][0] for action in ('canvas.copy','canvas.download')]
    await service.dispatch('canvas.versions.revise', {'id':identity,'expectedRevision':1,'content':'<h1>Newer</h1>'})
    for effect in effects:
        assert effect['url'].endswith('?version=1')
        response = await client.get(effect['url'])
        assert response.status == 200
        assert await response.text() == content
    for version in ('999','invalid','0'):
        response = await client.get(f'/api/canvas/{identity}/source?version={version}')
        assert response.status in {400,404}


async def test_source_and_download_preserve_small_large_small_file_versions(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    host = await create_app(tmp_path/'app', preload_providers=False, workspace=tmp_path,
                            runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(host)
    service = host['service']
    await service.dispatch('session.create', {})
    path = tmp_path/'versions.html'
    contents = ['<h1>First small</h1>', '<h1>Large</h1><!--' + 'x' * 1_100_000 + '-->', '<h1>Second small</h1>']
    for content in contents:
        path.write_text(content)
        result = await service.dispatch('canvas.show', {'kind': 'auto', 'path': str(path)})
    identity = result['result']['id']
    path.unlink()
    for version, content in enumerate(contents, 1):
        await service.dispatch('canvas.select', {'id': identity, 'version': version})
        document = await client.get(f'/api/canvas/{identity}/document')
        assert document.status == 200
        assert (await document.text()).endswith(content)
        for endpoint in ('source', 'download'):
            # Explicit chat links are immutable; unversioned URLs mean latest.
            for suffix in ('', '?version=' + str(version)):
                response = await client.get(f'/api/canvas/{identity}/{endpoint}' + suffix)
                assert response.status == 200
                assert await response.text() == (content if suffix else contents[-1])
