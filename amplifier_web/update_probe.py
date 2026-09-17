"""No-model compatibility probe in a staged environment, never a user session."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from amplifier_web.host.session import prepare_manager

async def main():
    async def deny(*args): return 'deny'
    session,runtime,report=await prepare_manager(sys.argv[1],bundle=sys.argv[2],resume=False,ask=deny)
    try:
        if not report.get('standalone') or not report.get('providers'): raise RuntimeError('Incomplete staged runtime')
        for name in ('amplifier_app_cli','amplifier_loop_live_cli','amplifier_workspace'):
            if importlib.util.find_spec(name): raise RuntimeError('A CLI host dependency was introduced')
    finally: await session.cleanup()
    print('VALIDATED')

asyncio.run(main())
