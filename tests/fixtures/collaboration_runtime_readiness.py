"""Install the app-selected runtime in an isolated DTU; no model request.

Run only in the caller's DTU. Prints module provenance/capabilities, never keys.
This is readiness and selected-source evidence, not live provider acceptance.
"""
import json
from pathlib import Path
import subprocess
import sys

from amplifier_web.runtime import RuntimeManager


def main():
    home = Path(sys.argv[1])
    home.mkdir(parents=True, exist_ok=True)
    manager = RuntimeManager()
    command = manager._command(home=home)
    probe = """
import importlib.metadata as metadata
import inspect
import json
import os
import sys
from amplifier_module_loop_live.runtime import Input
import amplifier_module_loop_live.orchestrator as orchestrator
from amplifier_web.builtin_behaviors import resource_root
source = inspect.getsource(orchestrator)
def distribution(name):
    value = metadata.distribution(name)
    raw = value.read_text('direct_url.json')
    return {'version': value.version, 'source': json.loads(raw) if raw else None}
print(json.dumps({
    'python': sys.version.split()[0],
    'loop': distribution('amplifier-module-loop-live'),
    'input_fields': list(inspect.signature(Input).parameters),
    'assistant_event_has_channel': 'channel' in source[source.find('\"assistant.message\"'):source.find('\"assistant.message\"') + 500],
    'finished_emitter_excerpt': source[max(0, source.find('\"generation.finished\"') - 100):source.find('\"generation.finished\"') + 700],
    'providers_importable': {name: __import__(name).__name__ for name in ('amplifier_module_provider_anthropic', 'amplifier_module_provider_openai')},
    'credential_environment_present': {name: bool(os.environ.get(name)) for name in ('ANTHROPIC_API_KEY','ANTHROPIC_FABLE_API_KEY')},
    'coordinate_skill_exists': (resource_root() / 'skills/coordinate-work/SKILL.md').is_file(),
    'provider_request_verified': False,
    'kind': 'installed-runtime readiness and selected-source inspection'
}, indent=2))
"""
    # Replace the existing worker script argument, not the runtime manifest or
    # install policy. Use the same release-selected native dependency project.
    subprocess.run([*command[:-1], "-c", probe], check=True)


if __name__ == "__main__":
    main()