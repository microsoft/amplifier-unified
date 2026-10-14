"""Explicit maintenance includes dormant owners without making them resident."""
import copy
import tarfile
import time

import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.management import Management
from amplifier_web.recovery import backup, reset
from amplifier_web.state_records import load, load_owner
from amplifier_web.updates import UpdateManager
from test_automatic_history import app_factory


def seed_local(app, workspace, *, count=4):
    rows=[]
    for number in range(count):
        row=app._new_session({'workspace':str(workspace),'title':f'Keep local title {number}'})
        row.update(createdAt=1,updatedAt=1,status='idle')
        row['messages']=[{'id':f'original-{number}','role':'assistant','text':f'Saved display {number}','createdAt':1}]
        SessionStore.for_app(app.data_dir,row['workspace']).save(row['id'],
            [{'role':'assistant','content':f'Canonical transcript {number}'}],
            {'working_dir':row['workspace'],'name':'Original imported title','bundle_name':'anchors'})
        rows.append(row)
    app._state['sessions'].extend(rows)
    app._state['selectedSessionId']=rows[0]['id']
    app._save()
    return rows


async def test_backup_includes_dormant_workspace_files_and_worker_without_admission(app_factory,tmp_path):
    app=app_factory()
    workspace=tmp_path/'dormant-workspace';workspace.mkdir()
    rows=seed_local(app,workspace)
    active=app._new_session({'title':'Selected elsewhere'})
    app._state['sessions'].append(active)
    app._state['selectedSessionId']=active['id']
    rows[-1]['workers']=[{'id':'saved-worker','status':'completed'}]
    SessionStore.for_app(app.data_dir,str(workspace)).save('saved-worker',
        [{'role':'assistant','content':'Saved worker transcript'}],{})
    settings=workspace/'.amplifier'/'settings.yaml'
    settings.parent.mkdir(exist_ok=True);settings.write_text('bundle: anchors\n')
    app._save()
    await app.close()
    restored=app_factory(home=app.data_dir)
    assert {row['id'] for row in restored._state['sessions']}=={active['id']}
    result=await backup(restored)
    with tarfile.open(result['backup']) as archive:
        names=archive.getnames()
        for row in rows:
            assert any(name.endswith('/'+row['id']+'/transcript.jsonl') for name in names)
        assert any(name.endswith('/saved-worker/transcript.jsonl') for name in names)
        assert any(name.startswith('workspace-settings/') and name.endswith('/settings.yaml') for name in names)
    assert {row['id'] for row in restored._state['sessions']}=={active['id']}
    assert not restored.runtime.started and not restored.runtime.sent


async def test_cleanup_previews_dormant_owners_preserves_recent_and_publishing_then_allows_reimport(app_factory,tmp_path,monkeypatch):
    app=app_factory()
    rows=seed_local(app,tmp_path)
    rows[2]['messages'][-1]['createdAt']=time.time()
    app._save()
    await app.close()
    restored=app_factory(home=app.data_dir)
    monkeypatch.setattr(restored.publishing,'owns_records',lambda identity:identity==rows[3]['id'])
    manager=Management(restored)
    before=copy.deepcopy(load_owner(restored.db,rows[1]['id']))
    await manager.perform('history.cleanup',{'days':1})
    assert {row['id'] for row in restored.state['cleanupPreview']['sessions']}=={rows[1]['id']}
    assert load_owner(restored.db,rows[1]['id'])==before
    assert {row['id'] for row in restored._state['sessions']}=={rows[0]['id']}
    await manager.perform('history.cleanup',{'days':1,'apply':True,'purge':True})
    assert load_owner(restored.db,rows[1]['id'])==(None,None)
    assert len(load(restored.db)['sessions'])==3
    assert restored.directory.resolve(rows[1]['id']) is None
    await restored.close()
    again=app_factory(home=app.data_dir)
    assert again.directory.resolve(rows[1]['id']) is None
    await Management(again).perform('history.import',{'id':rows[1]['id']})
    assert again.directory.resolve(rows[1]['id']) is not None
    assert len(load(again.db)['sessions'])==4
    assert again._session()['messages'][0]['text']=='Canonical transcript 1'
    assert not again.runtime.started and not again.runtime.sent


async def test_import_selects_dormant_local_owner_without_replacing_saved_presentation(app_factory,tmp_path):
    app=app_factory()
    rows=seed_local(app,tmp_path)
    target=rows[-1]
    target['selection']={'instance':'openai','model':'fixture-model','effort':'high'}
    app._save()
    await app.close()
    restored=app_factory(home=app.data_dir)
    assert target['id'] not in {row['id'] for row in restored._state['sessions']}
    await Management(restored).perform('history.import',{'id':target['id']})
    selected=restored._session()
    assert selected['id']==target['id']
    assert selected['title']==target['title']
    assert selected['selection']==target['selection']
    assert selected['messages']==target['messages']
    assert len(load(restored.db)['sessions'])==len(rows)
    assert not restored.runtime.started and not restored.runtime.sent


async def test_reset_forgets_dormant_records_and_preserves_private_backup(app_factory,tmp_path):
    app=app_factory()
    rows=seed_local(app,tmp_path)
    await app.close()
    restored=app_factory(home=app.data_dir)
    assert len(restored._state['sessions'])==1
    manager=Management(restored);restored.management=manager
    restored.update_manager=UpdateManager(restored)
    await reset(manager,{'parts':['conversations'],'apply':True,'confirmation':'RESET'})
    assert not load(restored.db)['sessions']
    assert not restored.directory.select(limit=-1)
    with tarfile.open(restored.state['maintenance']['backup']) as archive:
        names=archive.getnames()
        assert all(any(name.endswith('/'+row['id']+'/transcript.jsonl') for name in names) for row in rows)
    await restored.close()
    again=app_factory(home=app.data_dir)
    assert not load(again.db)['sessions']
    assert not again._state['sessions']


async def test_reset_rejects_dormant_publishing_owner_before_replacing_runtime(app_factory,tmp_path,monkeypatch):
    app=app_factory()
    rows=seed_local(app,tmp_path)
    await app.close()
    restored=app_factory(home=app.data_dir)
    monkeypatch.setattr(restored.publishing,'owns_records',lambda identity:identity==rows[-1]['id'])
    original=restored.runtime
    with pytest.raises(ValueError,match='publishing releases'):
        await reset(Management(restored),{'parts':['conversations'],'apply':True,'confirmation':'RESET'})
    assert restored.runtime is original
    assert len(load(restored.db)['sessions'])==len(rows)


async def test_update_retention_protects_generation_referenced_only_by_dormant_workspace(app_factory, tmp_path, monkeypatch):
    from amplifier_web import update_retention
    from test_update_retention import manager_fixture

    app = app_factory()
    active = app._new_session({'title': 'Active'})
    dormant = app._new_session({'title': 'Dormant'})
    dormant['workspace'] = str(tmp_path / 'dormant-project')
    app._state['sessions'] = [active, dormant]
    app._state['selectedSessionId'] = active['id']
    app._save()
    await app.close()
    restored = app_factory(home=app.data_dir)
    assert [row['id'] for row in restored._state['sessions']] == [active['id']]
    configuration_references = update_retention.configuration_references
    manager, names, _ = manager_fixture(tmp_path / 'generation-fixture', monkeypatch)
    manager.service = restored
    retained = manager.directory / 'releases' / names[3]
    settings = tmp_path / 'dormant-project' / '.amplifier' / 'settings.yaml'
    settings.parent.mkdir(parents=True)
    settings.write_text('module: ' + str(retained / 'source') + '\n')
    monkeypatch.setattr(update_retention, 'configuration_references', configuration_references)
    from amplifier_web import state_records
    monkeypatch.setattr(state_records, 'load', lambda *_: pytest.fail('Retention loaded all saved metadata'))
    monkeypatch.setattr(restored.directory, 'decode', lambda *_: pytest.fail('Retention decoded session metadata'))
    assert str(retained) in configuration_references(manager)
    assert (await update_retention.reclaim(manager))['removed'] == 1
    assert retained.is_dir()
    assert not (manager.directory / 'releases' / names[2]).exists()
    assert [row['id'] for row in restored._state['sessions']] == [active['id']]


async def test_update_retention_probes_native_workspace_paths_without_decoding_27000_owners(app_factory, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import update_retention
    from amplifier_web.native_catalog import NativeCatalog
    from amplifier_web.session_files import amplifier_home
    from test_native_directory import seed

    app = app_factory()
    catalog = NativeCatalog(app.data_dir / 'native-catalog.sqlite3', amplifier_home())
    _, workspace = seed(catalog, tmp_path / 'archive')
    app.directory.bind(catalog)
    manager = SimpleNamespace(home=app.data_dir, service=app)
    monkeypatch.setattr(app.directory, 'decode', lambda *_: pytest.fail('Retention decoded native metadata'))
    calls = []
    async def probe(operation, values, *, timeout):
        calls.append((operation, values, timeout))
        return 'reference-check-completed'
    monkeypatch.setattr(update_retention, '_probe', probe)
    assert await update_retention.inspect_references(manager, timeout=2) == 'reference-check-completed'
    assert calls == [('references', {'home': str(app.data_dir), 'workspaces': sorted({
        app._state['settings']['workspace'], workspace['path']})}, 2)]
    assert app._state['sessions'] == []
