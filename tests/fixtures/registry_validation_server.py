"""Real shared registry actions with an isolated deterministic validator process."""
import asyncio
from pathlib import Path
import sys
import tempfile
from aiohttp import web
import settings_ui_server as base
from amplifier_web.registry import RegistryManager

async def serve():
    with tempfile.TemporaryDirectory(prefix='registry-validation-') as tmp:
        home=Path(tmp)
        app=await base.main(home)
        (home/'workspace'/'valid-candidate').mkdir()
        helper=home/'validator.py'
        helper.write_text('import sys,json,time\na=json.loads(sys.stdin.readline())\ntime.sleep(.15)\nprint(json.dumps({"type":"module.validation","id":a["id"],"passed":"valid-candidate" in a.get("source",""),"checks":[]}))\n')
        original=RegistryManager.__init__
        def init(self,*args,**kwargs):
            original(self,*args,**kwargs)
            self.validation_command=[sys.executable,str(helper)]
        RegistryManager.__init__=init
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}',flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(serve())
