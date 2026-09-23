import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.host.session import _apply_host_policy
from test_output_images import png


def receipt(root, name, *, inputs=None):
    image = png(3 if inputs else 2, 2)
    (root / (name + '.png')).write_bytes(image)
    row = {'schema': 'amplifier.image.receipt.v1', 'status': 'completed',
        'requestId': name, 'requestHash': 'a' * 64, 'backend': 'fixture', 'model': 'fixture-image',
        'operation': 'edit' if inputs else 'generate', 'inputs': inputs or [],
        'artifact': {'path': name + '.png', 'sha256': hashlib.sha256(image).hexdigest(), 'bytes': len(image),
                     'width': 3 if inputs else 2, 'height': 2, 'mimeType': 'image/png', 'mode': 'RGB'}}
    (root / (name + '.json')).write_text(json.dumps(row))
    return row, image


async def test_shared_image_attachment_preserves_exact_bytes_parent_and_passive_state(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        app.state['view']['draft'] = 'Unsent'
        first, image = receipt(tmp_path, 'first')
        generated = (await app.app_bridge('dispatch', {'action': 'outputs.attachImage',
            'args': {'title': 'Original', 'receiptPath': 'first.json'}, 'id': 'image-attach'}, sid))['result']
        assert generated['sha256'] == first['artifact']['sha256']
        assert generated['imageGeneration']['provenance'] == 'producer-reported; local artifact bytes verified'
        edited, next_image = receipt(tmp_path, 'edit', inputs=[{
            'path': 'first.png', 'sha256': generated['sha256'], 'role': 'target'}])
        args = {'sessionId': sid, 'title': 'Edit', 'receiptPath': 'edit.json'}
        with pytest.raises(AppError, match='original image first'):
            await app.dispatch('outputs.attachImage', args)
        second = (await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']}, command_id='attach-edit'))['result']
        duplicate = (await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']}, command_id='attach-edit'))['result']
        assert duplicate['id'] == second['id']
        assert second['parentId'] == generated['id'] and second['version'] == 2
        assert (tmp_path / 'first.png').read_bytes() == image
        (tmp_path / 'edit.png').write_bytes(b'changed after snapshot')
        assert app.outputs.content(second) == next_image
        observed = await app.app_bridge('outputs.image.read', {'id': second['id'], 'sha256': second['sha256']}, sid)
        assert observed['width'] == 3 and observed['evidence'] == 'immutable-output-snapshot'
        assert app.state['selectedSessionId'] == sid and app.state['view']['draft'] == 'Unsent'
        with pytest.raises(AppError, match='PNG|receipt'):
            await app.dispatch('outputs.attachImage', {**args, 'parentId': generated['id']})
    finally:
        await app.close()


@pytest.mark.parametrize('corruption', ['unknown', 'hash', 'dimensions', 'parent', 'outside', 'extra'])
async def test_bad_receipts_are_not_accepted(tmp_path, corruption):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        row, _ = receipt(tmp_path, 'bad')
        if corruption == 'unknown':
            row['status'] = 'unknown'
        elif corruption == 'hash':
            row['artifact']['sha256'] = '0' * 64
        elif corruption == 'dimensions':
            row['artifact']['width'] = 20
        elif corruption == 'parent':
            row['operation'] = 'edit'
        elif corruption == 'outside':
            row['artifact']['path'] = str(tmp_path.parent / 'outside.png')
        elif corruption == 'extra':
            row['credential'] = 'must not echo this value'
        (tmp_path / 'bad.json').write_text(json.dumps(row))
        with pytest.raises(AppError) as error:
            await app.dispatch('outputs.attachImage', {'sessionId': sid, 'title': 'Bad', 'receiptPath': 'bad.json'})
        assert 'must not echo' not in str(error.value)
        assert app.outputs.store.list(sid)['items'] == []
    finally:
        await app.close()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('section', ['modules', 'config', 'overrides'])
def test_image_writer_inherits_effective_host_denials(tmp_path, child, section):
    from amplifier_foundation import Bundle
    settings = {'allowed_write_paths': [str(tmp_path / 'extra')], 'denied_write_paths': ['private']}
    settings = ({'overrides': {'tool-filesystem': {'config': settings}}} if section == 'overrides'
                else {section: {'tools': [{'module': 'tool-filesystem', 'config': settings}]}})
    declaration = {'module': 'tool-image', 'config': {'backend': 'fixture', 'denied_write_paths': ['images-private']}}
    bundle = Bundle(name='images', tools=[] if child else [declaration],
                    agents={'worker': {'tools': [declaration]}} if child else {})
    config = SimpleNamespace(settings=settings, workspace=tmp_path / 'history')
    _apply_host_policy(bundle, config, execution_workspace=tmp_path / 'checkout')
    row = bundle.agents['worker']['tools'][0] if child else bundle.tools[0]
    assert row['config']['backend'] == 'fixture'
    assert set(row['config']['denied_write_paths']) == {str(tmp_path / 'checkout/private'), str(tmp_path / 'checkout/images-private')}
    assert str(tmp_path / 'checkout') in row['config']['allowed_write_paths']


def test_declared_filesystem_denial_cannot_be_bypassed_by_image_tool(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image'},
        {'module': 'tool-filesystem', 'config': {'denied_write_paths': ['private']}}],
        agents={'child': {'tools': [{'module': 'tool-image'}]}})
    _apply_host_policy(bundle, SimpleNamespace(settings={}, workspace=tmp_path))
    for row in (bundle.tools[0], bundle.agents['child']['tools'][0]):
        assert row['config']['denied_write_paths'] == [str(tmp_path / 'private')]


def test_child_declaration_denial_is_not_applied_to_parent_or_sibling(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image'}, {'module': 'tool-filesystem'}],
        agents={'restricted': {'tools': [{'module': 'tool-image'},
            {'module': 'tool-filesystem', 'config': {'denied_write_paths': ['artifacts/images']}}]},
            'sibling': {'tools': [{'module': 'tool-image'}]}})
    _apply_host_policy(bundle, SimpleNamespace(settings={}, workspace=tmp_path))
    assert not bundle.tools[0]['config'].get('denied_write_paths')
    assert not bundle.agents['sibling']['tools'][0]['config'].get('denied_write_paths')
    assert bundle.agents['restricted']['tools'][0]['config']['denied_write_paths'] == [str(tmp_path / 'artifacts/images')]


def test_image_uploads_inherit_shared_and_declared_read_restrictions(tmp_path):
    from amplifier_foundation import Bundle
    bundle = Bundle(name='images', tools=[{'module': 'tool-image', 'config': {'allowed_read_paths': ['inputs'], 'denied_read_paths': ['inputs/own']}},
        {'module': 'tool-filesystem', 'config': {'denied_read_paths': ['inputs/private']}}])
    settings = {'config': {'tools': [{'module': 'tool-filesystem', 'config': {'denied_read_paths': ['inputs/shared']}}]}}
    _apply_host_policy(bundle, SimpleNamespace(settings=settings, workspace=tmp_path))
    row = bundle.tools[0]['config']
    assert set(row['denied_read_paths']) == {str(tmp_path / path) for path in ('inputs/own', 'inputs/private', 'inputs/shared')}
    assert row['allowed_read_paths'] == ['inputs']
