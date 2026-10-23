import json
from types import SimpleNamespace

import pytest

from amplifier_web.module_failures import ConfiguredModuleError, persist_failures, read_failures, safe_failures
from amplifier_web.provider_environment import materialize_bundle_providers
from amplifier_web.provider_failure_diagnostics import diagnose, safe_diagnostic
from amplifier_web.provider_isolation import ProviderIsolation, UnavailableProvider


def connection(name='account'):
    return {'module': 'provider-fixture', 'instance_id': name, 'source': 'private-source',
            'config': {'api_key': 'private-key', 'default_model': 'fixture'}}


@pytest.mark.parametrize('error,stage,category', [
    (ModuleNotFoundError('private-package private-key'), 'preparation', 'missing_dependency'),
    (ImportError('private-path'), 'schema', 'import_failure'),
    (ValueError('authentication invalid_api_key private-key'), 'mount', 'unknown'),
    (ValueError('private-config'), 'configuration', 'configuration'),
    (ValueError('private-schema'), 'schema', 'schema'),
    (TimeoutError('private-endpoint'), 'mount', 'timeout'),
    (ConnectionError('private-endpoint'), 'preparation', 'connection'),
])
def test_classification_uses_observed_type_and_stage_without_guessing_from_text(error, stage, category):
    actual = diagnose(error, stage=stage)
    assert actual == {'stage': stage, 'category': category, 'errorType': type(error).__name__}
    assert 'private' not in json.dumps(actual)


@pytest.mark.parametrize('status,category', [(401, 'authentication'), (403, 'permission'),
    (429, 'rate_limit'), (503, 'service'), (400, 'invalid_request')])
def test_observed_http_status_survives_without_response_payload(status, category):
    error = RuntimeError('private-key')
    error.status_code = status
    error.response = {'Authorization': 'private-key'}
    actual = diagnose(error, stage='mount')
    assert actual == {'stage': 'mount', 'category': category, 'errorType': 'RuntimeError', 'httpStatus': status}


@pytest.mark.parametrize('raw', [None, 'private', {'stage': {}}, {'stage': []}, {'stage': 'private'}])
def test_untrusted_diagnostic_shape_is_ignored(raw):
    assert safe_diagnostic(raw) is None


def test_public_diagnostic_is_idempotent_finite_and_drops_extra_fields(tmp_path):
    raw = [{'module': 'provider-fixture', 'type': 'provider', 'instance_id': 'account',
            'reason_code': 'provider_mount_failed', 'config': {'api_key': 'private-key'},
            'diagnostic': {'stage': 'mount', 'category': ['private'], 'errorType': 'PrivateSecretClass',
                           'httpStatus': True, 'message': 'private-key', 'traceback': 'private-path'}}]
    actual = safe_failures(raw)
    assert actual[0]['diagnostic'] == {'stage': 'mount', 'category': 'unknown', 'errorType': 'Error'}
    assert safe_failures(actual) == actual
    persist_failures(tmp_path, raw)
    assert read_failures(tmp_path) == actual
    assert 'private' not in json.dumps(actual).lower()
    assert 'private' not in str(ConfiguredModuleError(raw)).lower()


async def test_preparation_and_marker_retain_only_instance_bound_safe_facts():
    policy = ProviderIsolation()
    row = connection()
    await policy.preparation_failed(SimpleNamespace(provider_spec=row,
        error=ModuleNotFoundError('private-key private-source')))
    failure = policy.failure(row)
    assert failure['instance_id'] == 'account'
    assert failure['diagnostic']['stage'] == 'preparation'
    assert failure['diagnostic']['category'] == 'missing_dependency'
    marker = UnavailableProvider(row, failure)
    assert marker.failure['diagnostic'] == failure['diagnostic']
    assert 'private' not in json.dumps(marker.failure)
    other = {**connection('other'), 'source': 'different-source'}
    assert policy.failure(other) is None


async def test_missing_environment_is_distinct_from_rejected_credentials_for_same_module_accounts():
    rows = [connection('missing-environment'), connection('missing-secret'), connection('healthy')]
    rows[0]['config']['api_key'] = '${UNAVAILABLE_PRIVATE_NAME}'
    rows[1]['config']['api_key'] = ''
    policy = ProviderIsolation()
    bundle = SimpleNamespace(providers=rows, agents={})
    async def schema(_):
        return {'fields': [{'id': 'api_key', 'field_type': 'secret', 'required': True}]}
    await materialize_bundle_providers(bundle, SimpleNamespace(mount_plan={}),
        environment={}, schema_loader=schema, isolation=policy)
    assert policy.failure(rows[0])['diagnostic']['category'] == 'missing_environment'
    assert policy.failure(rows[1])['diagnostic']['category'] == 'missing_credential'
    public = ConfiguredModuleError(list(policy.failures.values()))
    assert 'environment reference is unavailable' in str(public)
    assert 'required credential is missing' in str(public)
    assert 'authentication rejection' not in str(public)
    assert policy.failure(rows[2]) is None
    assert 'PRIVATE_NAME' not in json.dumps(list(policy.failures.values()))
    assert 'private-key' not in json.dumps(list(policy.failures.values()))


async def test_mount_failure_keeps_unknown_cause_and_never_copies_exception_text():
    policy = ProviderIsolation()
    capabilities, mounted = {}, {}
    async def mount(kind, value, name):
        mounted[name] = value
    coordinator = SimpleNamespace(register_capability=capabilities.__setitem__, mount=mount)
    await policy.before_initialize(SimpleNamespace(coordinator=coordinator))
    await capabilities['provider.load_failure'](None, connection(), ValueError('invalid_api_key private-key'))
    actual = mounted['account'].failure
    assert actual['diagnostic'] == {'stage': 'mount', 'category': 'unknown', 'errorType': 'ValueError'}
    assert 'private-key' not in json.dumps(actual)
