"""Public Foundation preparation and Core contract validation in isolation."""
import asyncio
import json
import importlib.metadata
import importlib
import contextlib
import io
import tempfile
import os
from pathlib import Path
import sys

async def main():
    args=json.loads(sys.stdin.readline())
    section=args['section'];module=args['module']
    kind={'tools':'tool','hooks':'hook','providers':'provider','orchestrator':'orchestrator','context':'context'}[section]
    try:
        from amplifier_foundation import Bundle
        from amplifier_core.validation import ToolValidator,HookValidator,ProviderValidator,OrchestratorValidator,ContextValidator
        entry={'module':module}
        if args.get('source'):entry['source']=args['source']
        data={'bundle':{'name':'module-validation'}}
        if section in {'orchestrator','context'}:data['session']={section:entry}
        else:data[section]=[entry]
        bundle=Bundle.from_dict(data)
        os.environ['AMPLIFIER_HOME']=str(Path(os.environ['AMPLIFIER_WEB_HOME'])/'foundation')
        await bundle.prepare(strict=True)
        validator={'tool':ToolValidator,'hook':HookValidator,'provider':ProviderValidator,'orchestrator':OrchestratorValidator,'context':ContextValidator}[kind]()
        # Importable module names let Core validators resolve packages prepared
        # by Foundation without exposing arbitrary file reads over the API.
        entry=next((entry for entry in importlib.metadata.entry_points(group='amplifier.modules') if entry.name==module),None)
        name=entry.value.split(':',1)[0] if entry else 'amplifier_module_'+module.replace('-','_')
        result=await validator.validate(name,config={'login_on_mount':False} if kind=='provider' else None)
        # Exception-derived messages may contain provider credentials. The
        # contract check name and outcome are sufficient for safe UI reporting.
        checks=[{'name':row.name,'passed':row.passed,'message':'Contract check passed.' if row.passed else 'Contract check failed; inspect this module implementation and dependencies.','severity':row.severity} for row in result.checks]
        behavioral=None
        if args.get('behavioral') and result.passed:
            behavioral=await asyncio.to_thread(run_behavioral,name,kind)
        passed=result.passed and (behavioral is None or behavioral['passed'])
        print(json.dumps({'type':'module.validation','id':args['id'],'passed':passed,'checks':checks,'method':'core-contract','behavioral':behavioral}),flush=True)
    except Exception as exc:
        print(json.dumps({'type':'module.validation','id':args['id'],'passed':False,'checks':[{'name':'prepare','passed':False,'message':'Module preparation or validation failed ('+type(exc).__name__+').','severity':'error'}],'method':'core-contract'}),flush=True)

def run_behavioral(name,kind):
    import pytest
    module=importlib.import_module(name)
    package=Path(module.__file__).parent
    class_name={'tool':'Tool','provider':'Provider','hook':'Hook','context':'Context','orchestrator':'Orchestrator'}[kind]+'BehaviorTests'
    reports=[]
    class Results:
        def pytest_runtest_logreport(self,report):
            if report.when=='call' or report.failed or report.skipped:
                reports.append({'name':report.nodeid.rsplit('::',1)[-1],'status':report.outcome})
    with tempfile.TemporaryDirectory(prefix='amplifier-contract-') as directory:
        target=Path(directory)/'test_contract.py'
        text='from amplifier_core.validation.behavioral import '+class_name+'\nclass TestModuleContract('+class_name+'):\n    pass\n'
        if kind=='provider':
            # The exported suites call the provider's model catalog but must
            # never implicitly start a device-login flow during module tests.
            text += '\nimport inspect,pytest,pytest_asyncio,importlib\n@pytest_asyncio.fixture\nasync def provider_module(coordinator):\n    mod=importlib.import_module('+repr(name)+')\n    cleanup=await mod.mount(coordinator,{"login_on_mount":False})\n    providers=coordinator.mount_points.get("providers",{})\n    assert providers,"No provider mounted; configure authentication before behavioral tests"\n    try: yield next(iter(providers.values()))\n    finally:\n        if cleanup:\n            value=cleanup()\n            if inspect.isawaitable(value):await value\n'
        target.write_text(text)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            code=pytest.main([str(target),'-q','-p','pytest_asyncio.plugin','-p','amplifier_core.pytest_plugin','--module-path='+str(package),'--asyncio-mode=auto'],plugins=[Results()])
    return {'passed':code==0 and any(row['status']=='passed' for row in reports),'exitCode':int(code),'tests':reports}

if __name__=='__main__':asyncio.run(main())
