"""New needs-input browser alerts are opt-in and separate from external delivery."""
import pytest

from amplifier_web.notifications import Notifications


def test_needs_input_preference_is_off_without_opt_in_and_preserves_connections(tmp_path):
    notifications = Notifications(tmp_path)
    assert notifications.public().get('needsInput', False) is False
    before = notifications.read()
    result = notifications.save({'needsInput': True})
    assert result['needsInput'] is True
    assert result['enabled'] is False  # Does not enable ntfy or create a subscription.
    assert {key: value for key, value in notifications.read().items() if key != 'needsInput'} == before
    assert Notifications(tmp_path).public()['needsInput'] is True
    notifications.save({'needsInput': False})
    assert notifications.public()['needsInput'] is False


@pytest.mark.parametrize('value', ['true', 1, None, {}])
def test_needs_input_rejects_non_boolean_without_writing(tmp_path, value):
    notifications = Notifications(tmp_path)
    with pytest.raises(ValueError, match='true or false'):
        notifications.save({'needsInput': value})
    assert not notifications.path.exists()