"""Actual legacy serializer/readback for an isolated continuation rehearsal."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

mode, legacy, destination = sys.argv[1:]
root = Path(destination).resolve()
sys.path.insert(0, legacy)
os.environ['AMPLIFIER_HOME'] = str(root / ('original-native' if mode == 'seed' else 'rollback-native'))
os.environ['AMPLIFIER_WEB_HOME'] = str(root / 'legacy-app')
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_files import project_slug

sid = 'd171ccd8-a11a-4c92-94f6-65054bda043b'
workspace = root / 'workspace'
store = SessionStore.for_app(root / 'legacy-app', workspace)
if mode == 'seed':
    workspace.mkdir()
    rows = [
        {'role': 'user', 'content': 'Keep the project phrase violet compass.', 'metadata': {
            'timestamp': '2026-10-01T12:00:00Z', 'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'original-typed-input'}}},
        {'role': 'assistant', 'content': 'The project phrase is **violet compass**.\n\n```json\n{"retained": true}\n```'},
    ]
    store.save(sid, rows, {'session_id': sid, 'working_dir': str(workspace), 'parent_id': None,
        'name': 'Retained continuation fixture', 'name_source': 'manual', 'status': 'idle'})
    saved = store.directory(sid)
    # A past uncertain effect must remain evidence, never a startup instruction.
    from amplifier_core.message_models import ToolCall
    from amplifier_module_loop_live.job_store import JobStore
    jobs = JobStore(saved / 'live-jobs')
    try: jobs.begin(ToolCall(id='retained-call', name='never_replay_legacy_effect', arguments={}), 'retained-job', 'Original unconfirmed dispatch')
    finally: jobs.close()
    (root / 'original-native/settings.yaml').write_text('bundle:\n  app: []\n')
    (workspace / 'before-switch.txt').write_text('Original workspace artifact\n')
    shutil.copytree(root / 'original-native', root / 'candidate-native')
    original = {p.relative_to(root / 'original-native').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (root / 'original-native').rglob('*') if p.is_file()}
    (root / 'original-hashes.json').write_text(json.dumps(original))
    provider = root / 'provider'; provider.mkdir()
    (provider / 'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-switch-fixture"\nversion="0.1.0"\n')
    module = provider / 'amplifier_module_provider_switch_fixture'; module.mkdir()
    (module / '__init__.py').write_text('''import json
from pathlib import Path
from amplifier_core.models import ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, Usage, ToolCall
class Provider:
    name='fixture'
    def __init__(self, config=None): self.root=Path((config or {}).get('directory','.'))
    def parse_tool_calls(self, response): return response.tool_calls or []
    def get_info(self): return ProviderInfo(id='fixture', display_name='Offline continuation fixture', defaults={'model':'fixture','max_tokens':4096}, capabilities=['tools'])
    async def list_models(self): return [{'id':'fixture'}]
    async def complete(self, request, **kwargs):
        text=str(request.messages)
        assert 'violet compass' in text, 'Prior conversation was lost'
        assert 'CONTINUE-MIGRATED-41' in text, 'Only the new explicit input may run'
        with (self.root/'provider-requests.jsonl').open('a') as f: f.write(json.dumps([m.model_dump(mode='json') for m in request.messages])+'\\n')
        if 'Continuation artifact saved' not in text:
            return ChatResponse(content=[], tool_calls=[ToolCall(id='write-continuation',name='fixture_save',arguments={})],finish_reason='tool_calls')
        return ChatResponse(content=[TextBlock(text='Continued with violet compass and saved the new artifact.')],finish_reason='stop',usage=Usage(input_tokens=12,output_tokens=10,total_tokens=22))
async def mount(coordinator, config=None):
    root=Path(config['directory'])
    class Save:
        name='fixture_save'; description='Save the isolated continuation artifact'; input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            with (root/'workspace/after-switch.txt').open('x') as f: f.write('violet compass\\n')
            with (root/'effects.jsonl').open('a') as f: f.write('write\\n')
            return ToolResult(success=True,output='Continuation artifact saved')
    await coordinator.mount('providers',Provider(config),name='fixture')
    await coordinator.mount('tools',Save(),name='fixture_save')
''')
    context = Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
    bundle = root / 'fixture.yaml'
    bundle.write_text('bundle:\n  name: legacy-continuation\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-switch-fixture\n    source: '+str(provider)+'\n    config:\n      directory: '+str(root)+'\n')
    (root / 'native.json').write_text(json.dumps({'home': str(root / 'candidate-native'), 'appHome': str(root / 'candidate-app'),
        'bundle': str(bundle), 'startupTimeout': 90, 'adminWorkspaceRoots': [str(workspace)]}))
    print(json.dumps({'nativeId': sid, 'originalRows': store.load(sid)[0], 'relativeDirectory': saved.relative_to(root/'original-native').as_posix()}))
else:
    assert mode == 'readback'
    from amplifier_web.automatic_history import read_transcript
    loaded, metadata = store.load(sid)
    page = read_transcript({'id': sid, 'nativeIdentity': sid, 'nativeProject': project_slug(str(workspace))})
    texts = [r['text'] for r in page['messages']]
    assert texts[0] == 'Keep the project phrase violet compass.'
    assert sum('CONTINUE-MIGRATED-41' in t for t in texts) == 1, texts
    assert texts[-1] == 'Continued with violet compass and saved the new artifact.', texts
    assert metadata['name'] == 'Retained continuation fixture'
    assert (workspace/'before-switch.txt').read_text() == 'Original workspace artifact\n'
    assert (workspace/'after-switch.txt').read_text() == 'violet compass\n'
    assert (root/'effects.jsonl').read_text() == 'write\n'
    retained = store.directory(sid)/'live-jobs'/('job-'+hashlib.sha256(b'retained-call').hexdigest()+'.json')
    job = json.loads(retained.read_text())
    assert job['status'] == 'interrupted' and json.loads(job['result'])['outcome'] == 'unconfirmed'
    print(json.dumps({'legacyVisibleMessages': len(texts), 'nativeRows': len(loaded), 'newInputOnce': True,
        'newArtifactReadable': True, 'oldArtifactUnchanged': True, 'unknownEvidenceRetained': True, 'manualTitleRetained': True}))
