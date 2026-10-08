"""Create saved task state with the original controller and disk serializer."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

legacy, destination = sys.argv[1:]
root = Path(destination).resolve()
sid = 'd171ccd8-a11a-4c92-94f6-65054bda043b'
os.environ['AMPLIFIER_WEB_HOME'] = str(root / 'legacy-app')
sys.path.insert(0, legacy)
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.task_continuity import TaskController

# Only the surrounding session is a fixture. The original task state machine,
# command fingerprints, history and RuntimeControls serializer create the data.
capabilities = {'live.continuation_guard_supported': True}
coordinator = SimpleNamespace(session_state={}, get=lambda _: None,
    get_capability=capabilities.get, register_capability=capabilities.__setitem__)
controls = RuntimeControls.__new__(RuntimeControls)
controls.coordinator = coordinator
controls.session = SimpleNamespace(session_id=sid)
controls.runtime = SimpleNamespace(generation=None)
controls.configurator = None
controls.capacity = SimpleNamespace(policy={}, receipts={}, last_denial=None)
controls.max_output_tokens = None
controls.selection = {}
controls.selection_cleared = False
controls.capabilities = lambda: {'goals': True}
controls.tasks = TaskController(controls)

async def seed():
    async def command(operation, identity, revision, **args):
        return await controls.tasks.perform(operation, dict(commandId=identity,
            expectedRevision=revision, **args))
    await command('task.create', 'legacy-completed-create', 0, objective='Archive the original report')
    await command('task.complete', 'legacy-completed-finish', 1,
        condition='Archive the original report', evidence=['Original report was saved'])
    await command('task.create', 'legacy-current-create', 2,
        objective='Prepare the violet compass report', constraints=['Keep original source files'],
        questionIds=['original-unanswered-question'], operationIds=['original-uncertain-operation'],
        artifactRefs=['before-switch.txt'], maxTurns=5)
    await command('task.update', 'legacy-current-correction', 3,
        correction='Use the revised copper totals', origin='ui')
    await command('task.pause', 'legacy-current-pause', 4)
    assert controls.coordinator.session_state['goal'] is None
    source = controls.state_path()
    original = source.read_bytes()
    expected = json.loads(original)
    target = root / 'candidate-app' / 'sessions' / sid / 'control-state.json'
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert not target.exists()
    shutil.copy2(source, target)
    receipt = {'source': str(source), 'sourceSha256': hashlib.sha256(original).hexdigest(),
        'nativeId': sid, 'task': expected['task'], 'history': expected['taskHistory'],
        'receipts': expected['taskReceipts'], 'bytesCopiedWithoutConversion': True}
    (root / 'legacy-task.json').write_text(json.dumps(receipt), encoding='utf8')
    print(json.dumps(receipt))

asyncio.run(seed())
