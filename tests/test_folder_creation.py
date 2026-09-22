from pathlib import Path
import asyncio
import pytest
from amplifier_web.locations import create_folder
from amplifier_web.service import AppService
from amplifier_web.management import Management

@pytest.mark.parametrize('name',['', '..', '.', '../escape', '/absolute', 'a/b', 'a\\b', ' leading', 'trailing '])
def test_folder_names_are_single_components(tmp_path,name):
    with pytest.raises(ValueError): create_folder(tmp_path,name)
    assert list(tmp_path.iterdir()) == []

def test_existing_files_folders_and_symlinks_are_never_replaced(tmp_path):
    existing=tmp_path/'existing';existing.mkdir();(existing/'keep').write_text('original')
    link=tmp_path/'link';link.symlink_to(existing)
    file=tmp_path/'file';file.write_text('original')
    for name in ['existing','link','file']:
        with pytest.raises(ValueError,match='already exists'):create_folder(tmp_path,name)
    assert (existing/'keep').read_text() == 'original' and file.read_text() == 'original' and link.is_symlink()

async def test_shared_folder_action_creates_only_folder_and_returns_operation(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    app.management=Management(app)
    try:
        sessions=list(app.state['sessions']);workspaces=list(app.state['workspaces'])
        receipt=await app.dispatch('locations.create',{'controlId':'new-chat-workspace','path':str(tmp_path),'name':'new project'},command_id='folder-test')
        assert receipt['operationId'] == 'folder-test'
        for _ in range(100):
            if app.state.get('actionStatus',{}).get('locations.create',{}).get('phase') in {'ready','error'}:break
            await asyncio.sleep(.01)
        assert app.state['actionStatus']['locations.create']['phase'] == 'ready'
        listing=app.state['locationListing']
        assert listing['path'] == str(tmp_path/'new project') and listing['createdBy']=='folder-test'
        assert Path(listing['path']).is_dir()
        assert app.state['sessions']==sessions and app.state['workspaces']==workspaces
        # Replayed receipt is idempotent and does not replace a later user file.
        (Path(listing['path'])/'keep').write_text('keep')
        await app.dispatch('locations.create',{'controlId':'new-chat-workspace','path':str(tmp_path),'name':'new project'},command_id='folder-test')
        assert (Path(listing['path'])/'keep').read_text()=='keep'
    finally:await app.close()
