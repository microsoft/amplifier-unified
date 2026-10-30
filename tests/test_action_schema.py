"""Domain schemas must be available without assembling the application service."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('domain', ['operations', 'computation', 'coordination'])
def test_domain_definitions_do_not_import_service(domain):
    result = subprocess.run([sys.executable, '-c', '''
import importlib
import sys
domain = importlib.import_module('amplifier_web.' + sys.argv[1])
definitions = domain.definitions()
assert definitions
assert 'amplifier_web.service' not in sys.modules
''', domain], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_service_reexports_original_builders_and_required_field_semantics():
    from amplifier_web import action_schema, service

    assert service.schema is action_schema.schema
    assert service.string is action_schema.string
    properties = {'first': action_schema.string(), 'second': action_schema.string(20)}
    value = action_schema.schema(properties)
    assert value['properties'] is properties
    assert value['required'] == ['first', 'second']
    assert value['additionalProperties'] is False
    assert properties['first']['maxLength'] == 16000
    assert properties['second']['maxLength'] == 20
    required = []
    assert action_schema.schema(properties, required)['required'] is required
    assert action_schema.schema() == {
        'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False,
    }
