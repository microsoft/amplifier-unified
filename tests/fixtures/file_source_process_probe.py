"""Two independent native tool mounts meet at an on-disk read barrier."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from amplifier_module_tool_filesystem import mount as filesystem_mount
from amplifier_module_tool_apply_patch import mount as patch_mount
from amplifier_web.file_source_guard import install_file_source_guard

async def main():
    directory, writer, identity, target = sys.argv[1:]
    directory = Path(directory)
    tools, capabilities = {}, {'session.working_dir': str(directory)}
    async def mount(kind, tool, name):
        tools[name] = tool
    async def emit(*args):
        pass
    coordinator = SimpleNamespace(mount=mount, get=lambda kind: tools if kind == 'tools' else None,
        get_capability=capabilities.get, register_capability=capabilities.__setitem__,
        hooks=SimpleNamespace(emit=emit))
    await filesystem_mount(coordinator, {})
    await patch_mount(coordinator, {'engine': 'function' if writer == 'function' else 'native'})
    await install_file_source_guard(coordinator, directory / 'locks')
    assert (await tools['read_file'].execute({'file_path': target})).success
    (directory / ('ready-' + identity)).touch()
    while not (directory / 'go').exists():
        await asyncio.sleep(.01)
    new = 'worker-' + identity
    if writer == 'write':
        result = await tools['write_file'].execute({'file_path': target, 'content': new + '\n'})
    elif writer == 'edit':
        result = await tools['edit_file'].execute({'file_path': target, 'old_string': 'first', 'new_string': new})
    elif writer == 'native':
        result = await tools['apply_patch'].execute({'type': 'update_file', 'path': target, 'diff': '@@\n-first\n+' + new})
    else:
        result = await tools['apply_patch'].execute({'patch': f'*** Begin Patch\n*** Update File: {target}\n@@\n-first\n+{new}\n*** End Patch'})
    print(json.dumps(result.model_dump()))

asyncio.run(main())
