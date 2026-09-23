"""Real Settings actions with disposable provider login and long catalog copy."""
import asyncio,sys
from pathlib import Path
import settings_collections_server as collections
from amplifier_web.setup import SetupManager
original_main=collections.main
original_login=SetupManager.start_login
async def login(self,provider,args,workspace,scope):
    self.auth_command=[sys.executable,str(Path(__file__).with_name('settings_login_fixture.py'))]
    return await original_login(self,provider,args,workspace,scope)
SetupManager.start_login=login
original_catalog=collections.base.SmartToolsManager.catalog
async def catalog(self):
    result=await original_catalog(self)
    for row in result['items']:
        row['description']='Find source material and review it with your team. '+('This longer author description includes setup details, examples, and advice that belongs on the detail page. '*5)
    await self._change(lambda state:state.update(catalog=result['items']))
    return result
collections.base.SmartToolsManager.catalog=catalog
if __name__=='__main__':asyncio.run(collections.serve())
