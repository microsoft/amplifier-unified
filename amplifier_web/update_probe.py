"""No-model compatibility probe in a staged environment, never a user session."""
import asyncio
import importlib.util
import json
import sys
if __package__:
    from .runtime_bootstrap import bootstrap_app_package
else:
    from runtime_bootstrap import bootstrap_app_package
bootstrap_app_package()
from amplifier_web.update_diagnostics import exception_type,PROBE_PREFIX

async def main():
    async def deny(*args): return 'deny'
    facts={'ok':False,'stage':'prepare'}
    session=None
    try:
        from amplifier_web.host.session import prepare_manager
        session,runtime,report=await prepare_manager(sys.argv[1],bundle=sys.argv[2],resume=False,ask=deny)
        facts.update(stage='capabilities',standalone=bool(report.get('standalone')),providersPresent=bool(report.get('providers')))
        if not facts['standalone'] or not facts['providersPresent']:raise RuntimeError('Incomplete staged runtime')
        facts['cliAbsent']=not any(importlib.util.find_spec(name) for name in ('amplifier_app_cli','amplifier_loop_live_cli','amplifier_workspace'))
        if not facts['cliAbsent']:raise RuntimeError('A CLI host dependency was introduced')
        facts.update(ok=True,stage='complete')
    except Exception as error:facts.update(ok=False,errorType=exception_type(error))
    finally:
        if session:
            try:await session.cleanup()
            except Exception as error:facts.update(ok=False,stage='cleanup',errorType=exception_type(error))
    print(PROBE_PREFIX+json.dumps(facts),flush=True)
    if not facts['ok']:raise SystemExit(1)

asyncio.run(main())
