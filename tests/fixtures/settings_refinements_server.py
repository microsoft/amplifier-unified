"""Disposable real-action fixture; credentials and provider catalogs are synthetic."""
import asyncio,json,os
from pathlib import Path
import settings_fidelity_server
import settings_collections_server as collections
import amplifier_web.setup as setup
from amplifier_web.host.config import write_private
setup.github_cli_token=lambda:'fixture-github-cli-token'
original=collections.main
async def main(home):
    app=await original(home)
    os.environ['OPENAI_API_KEY']='fixture-environment-key'
    manager=setup.SetupManager(home);workspace=str(home/'workspace')
    path=home/'oauth.json';write_private(path,json.dumps({'access_token':'fixture-oauth','refresh_token':'fixture-refresh'}))
    await manager.perform('providers.save',{'workspace':workspace,'module':'provider-openai-chatgpt','id':'chatgpt-connected','config':{'default_model':'fixture-model','token_file_path':str(path)}})
    app['service'].state['updates']['application']={'current':'0.20.15','latest':'0.20.15','status':'current','releaseNotes':[{'version':'0.20.15','title':'Fixture release','changes':['Settings are simpler.'],'notices':[{'id':'fixture','title':'New Settings layout','detail':'Find everyday settings in the sidebar.','action':'Review the new layout.'}]}]}
    return app
collections.main=main
if __name__=='__main__':asyncio.run(collections.serve())
