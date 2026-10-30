"""One error identity across service and domain consumers, without a service dependency."""
import subprocess
import sys

import pytest

from amplifier_web.errors import AppError


def test_error_module_import_does_not_load_service_assembly():
    result = subprocess.run([sys.executable, '-c', '''
import sys
from amplifier_web.errors import AppError
assert 'amplifier_web.service' not in sys.modules
assert issubclass(AppError, Exception)
'''], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_service_export_catches_the_same_domain_error():
    from amplifier_web.service import AppError as ServiceAppError

    assert ServiceAppError is AppError
    error = AppError('The original outcome is unknown.', 503,
                     code='outcome_unknown', receipt={'accepted': False})
    with pytest.raises(ServiceAppError) as caught:
        raise error
    assert caught.value is error


@pytest.mark.parametrize('options', [
    {},
    {'status': 409, 'code': 'conflict', 'receipt': {}},
    {'status': 503, 'code': 'outcome_unknown', 'receipt': {'accepted': False}},
])
def test_error_preserves_message_status_code_and_original_receipt(options):
    error = AppError('Original explanation', **options)
    assert error.args == ('Original explanation',)
    assert str(error) == 'Original explanation'
    assert error.status == options.get('status', 400)
    assert error.code == options.get('code')
    assert error.receipt is options.get('receipt')
