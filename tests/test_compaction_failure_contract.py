"""Cross-package contract, also run explicitly with the owned standalone runtime."""
import json

import pytest

from amplifier_web.runtime import normalize_event
from amplifier_web.session_health import generation_failure


@pytest.mark.parametrize('code', [
    'native_input_oversized', 'native_checkpoint_invalid', 'native_no_reduction',
    'native_measurement_unavailable', 'native_compaction_failed', 'disabled',
    'request_context_unavailable', 'invalid_native_contract',
    'authoritative_measurement_unavailable', 'summary_output_limit', 'summary_empty',
    'previous_failure', 'summary_failed', 'native_failed',
])
def test_real_compaction_error_reaches_app_without_private_payload(code):
    errors = pytest.importorskip('amplifier_module_context_managed.errors')
    failures = pytest.importorskip('amplifier_module_loop_live.failures')
    error = errors.CompactionError(code, 'PRIVATE transcript and provider payload')
    event = {'type': 'generation.failed', 'generation_id': 'generation',
             'input_ids': ['input'], **failures.turn_failure(error)}
    _, payload = normalize_event(event, 'chat')
    visible = generation_failure(payload)
    assert visible['code'] == code
    assert visible['category'] == 'context_compaction'
    assert visible['stage'] == 'context_preparation'
    assert visible['replayed'] is False
    assert 'PRIVATE' not in json.dumps([event, payload, visible])
