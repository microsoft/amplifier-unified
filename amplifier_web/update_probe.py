"""No-model compatibility probe in a staged environment, never a user session."""
import asyncio
import argparse
from pathlib import Path
import importlib.util
import json
import sys
if __package__:
    from .runtime_bootstrap import bootstrap_app_package
else:
    from runtime_bootstrap import bootstrap_app_package
bootstrap_app_package()
from amplifier_web.update_diagnostics import exception_type,probe_failure,PROBE_PREFIX

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("bundle")
    parser.add_argument("--refresh-dependencies", action="store_true")
    parser.add_argument("--install-overrides", type=Path)
    args = parser.parse_args()
    async def deny(*args): return 'deny'
    facts={'ok':False,'stage':'prepare'}
    session=None
    try:
        from amplifier_web.host.session import prepare_manager
        session,runtime,report=await prepare_manager(args.workspace,bundle=args.bundle,resume=False,ask=deny,
            refresh_dependencies=args.refresh_dependencies, install_overrides=args.install_overrides)
        facts.update(stage='capabilities',standalone=bool(report.get('standalone')),providersPresent=bool(report.get('providers')))
        if not facts['standalone'] or not facts['providersPresent']:raise RuntimeError('Incomplete staged runtime')
        facts['cliAbsent']=not any(importlib.util.find_spec(name) for name in ('amplifier_app_cli','amplifier_loop_live_cli','amplifier_workspace'))
        if not facts['cliAbsent']:raise RuntimeError('A CLI host dependency was introduced')
        facts.update(ok=True,stage='complete')
    except Exception as error:facts.update(probe_failure(error,facts['stage']))
    finally:
        if session:
            try:await session.cleanup()
            except Exception as error:facts.update(ok=False,stage='cleanup',errorType=exception_type(error))
    print(PROBE_PREFIX+json.dumps(facts),flush=True)
    if not facts['ok']:raise SystemExit(1)

asyncio.run(main())
