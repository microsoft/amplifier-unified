import pytest
from amplifier_unified_feedback.redaction import redact


@pytest.mark.parametrize('text', [
    '{"token":"private-value-abcdef"}',
    "{'TOKEN': 'private-value-abcdef'}",
    'token=private-value-abcdef',
    '{"nested":{"credential":"private-value-abcdef"}}',
    'access_token: private-value-abcdef',
    'refresh-token="private-value-abcdef"',
])
def test_generic_secret_assignments_are_removed(text):
    cleaned, counts, warnings = redact(text)
    assert 'private-value-abcdef' not in cleaned
    assert {'kind': 'CREDENTIAL', 'count': 1} in counts
    assert warnings


def test_token_usage_metrics_and_ordinary_prose_remain():
    text = '{"input_tokens":42,"token_count":8,"tokenizer":"standard"} A token is a unit.'
    assert redact(text)[0] == text
