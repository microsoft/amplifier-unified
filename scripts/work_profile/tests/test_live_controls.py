"""Guard the live harness boundary without invoking a provider or UI approval."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import amplifier_foundation as foundation
import pytest
import yaml


@pytest.mark.asyncio
async def test_overlay_preserves_bundle_context_provider_and_limits_policy_override(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(script))
    spec = importlib.util.spec_from_file_location('live_controls', script / 'live_controls_acceptance.py')
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    for name in ('worktrees/parity-loop-integration', 'worktrees/parity-context-checkpoints/modules/context-managed',
                 'worktrees/parity-context-checkpoints/modules/tool-transcript', 'worktrees/parity-managed-process',
                 'worktrees/parity-truthful-web', 'repos/amplifier-module-tool-exec'):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    source = tmp_path / 'bundle.md'
    source.write_text('''---
bundle:
  name: source-bundle
providers:
  - module: provider-example
    config:
      default_model: exact-model
      reasoning_effort: high
session:
  context:
    config:
      engine: boundary
context:
  include:
    - source-bundle:context/keep.md
---
Keep the original instruction.
''')
    (tmp_path / 'context').mkdir()
    (tmp_path / 'context/keep.md').write_text('Retain this namespaced context.')
    out = tmp_path / 'output'
    out.mkdir()
    overlay = await harness.profile(out, SimpleNamespace(module_root=tmp_path, bundle=source))
    plan = yaml.safe_load(overlay.read_text().split('---', 2)[1])
    assert plan['includes'] == [{'bundle': str(source)}]
    assert 'providers' not in plan
    bash = next(row for row in plan['tools'] if row['module'] == 'tool-bash')
    assert bash['config']['safety_profile'] == 'unrestricted'
    assert bash['config']['require_approval'] is True
    assert plan['hooks'][0]['config']['rules'] == []
    assert plan['hooks'][0]['config']['tools']['compute']['require_approval'] is True
    loaded = await foundation.load_bundle(str(overlay), strict=True)
    assert loaded.providers[0]['config'] == {'default_model': 'exact-model', 'reasoning_effort': 'high'}
    assert loaded.session['context']['config']['engine'] == 'boundary'
    assert 'Keep the original instruction.' in loaded.instruction
    assert 'source-bundle:context/keep.md' in str(loaded._pending_context)
    assert loaded.source_base_paths['source-bundle'] == tmp_path


@pytest.mark.asyncio
async def test_completion_uses_public_events_after_projection_adoption(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(script))
    spec = importlib.util.spec_from_file_location('live_controls_events', script / 'live_controls_acceptance.py')
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    observation = SimpleNamespace(events=[{'kind': 'assistant.message', 'sessionId': 'owned', 'text': 'DONE'}])
    service = SimpleNamespace(_session=lambda sid: {'messages': [], 'status': 'idle'})
    await harness.finished(observation, service, 'owned', 'DONE')
    assert harness.assistant_events(observation, 'other') == ''


def test_cleanup_changes_only_fixture_copies_and_never_database_or_symlink(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from acceptance_cleanup import credential_values, redact_generated_credentials
    needle = 'synthetic-credential-value-123'
    selected = credential_values({'config': {'api_key': needle, 'default_model': 'model'}})
    folder = tmp_path / 'fixture'
    folder.mkdir()
    (folder / 'workspace').mkdir()
    (folder / 'report.json').write_text('{}')
    (folder / 'settings.yaml').write_text('api_key: ' + needle)
    (folder / 'app.sqlite3').write_bytes(needle.encode())
    outside = tmp_path / 'original-settings.yaml'
    outside.write_text(needle)
    (folder / 'linked.yaml').symlink_to(outside)
    report = redact_generated_credentials(folder, selected)
    assert report['redacted_files'] == report['database_matches'] == report['remaining_matches'] == 1
    assert (folder / 'settings.yaml').read_text() == 'api_key: [REDACTED]'
    assert (folder / 'app.sqlite3').read_bytes() == needle.encode()
    assert outside.read_text() == needle
    assert needle not in str(report)
