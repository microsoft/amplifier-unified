"""Provider-free actual Core/Foundation mount of the candidate file/search tools."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_web.host.session import _apply_host_policy
from amplifier_web.host.children import child_plan
from amplifier_web.read_policy import validate_mounted_read_policy
from amplifier_web.file_source_guard import install_file_source_guard


async def run():
    modules = ('loop-live', 'context-simple', 'tool-filesystem', 'tool-search')
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in modules}
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        os.environ['AMPLIFIER_WEB_HOME'] = str(home / 'app')
        workspace, external = home / 'workspace', home / 'installed'
        workspace.mkdir()
        external.mkdir()
        source = external / 'producer.py'
        source.write_text('public_source = True\n')
        bundle = Bundle(name='read-policy-probe', session={
            'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            tools=[{'module': 'tool-filesystem'}, {'module': 'tool-search'}])
        _apply_host_policy(bundle, SimpleNamespace(workspace=workspace, settings={}))
        plan = bundle.to_mount_plan()
        for label, mount_plan in [('root', plan), ('fresh-child', child_plan(plan, {}, working_dir=workspace)),
                ('retained-child', child_plan(plan, {}, working_dir=workspace, retained_image_tools=plan['tools']))]:
            prepared = PreparedBundle(mount_plan, BundleModuleResolver(paths), bundle)
            session = await prepared.create_session(session_id='read-policy-' + label, session_cwd=workspace)
            try:
                validate_mounted_read_policy(session.coordinator)
                await install_file_source_guard(session.coordinator, home / 'locks')
                tools = session.coordinator.get('tools')
                assert (await tools['read_file'].execute({'file_path': str(source)})).success
                assert (await tools['grep'].execute({'path': str(source), 'pattern': 'public_source', 'output_mode': 'content'})).output['matches_count'] == 1
                assert (await tools['glob'].execute({'path': str(external), 'pattern': '*.py'})).output['count'] == 1
                assert not (await tools['write_file'].execute({'file_path': str(source), 'content': 'changed'})).success
                assert source.read_text() == 'public_source = True\n'
                assert not session.coordinator.get('providers')
                checks.append(label)
            finally:
                await session.cleanup()
    origins = {name: importlib.util.find_spec(name).origin for name in (
        'amplifier_core', 'amplifier_foundation', 'amplifier_module_tool_filesystem', 'amplifier_module_tool_search')}
    print(json.dumps({'checked': checks, 'ordinary_external_reads': True, 'outside_writes_denied': True,
                      'providers_mounted': 0, 'provider_calls': 0, 'origins': origins}))


asyncio.run(run())
