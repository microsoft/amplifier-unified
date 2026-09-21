import hashlib

from amplifier_web.default_typography import upgrade_default


def test_only_exact_old_defaults_upgrade(monkeypatch):
    old = '#amp-one{font-size:12px}'
    new = '#amp-one{font-size:14px}'
    monkeypatch.setattr('amplifier_web.default_typography.PREVIOUS_DEFAULTS', {hashlib.sha256(old.encode()).hexdigest()})
    for name, css, expected in [('Amplifier Unified', old, new), ('Custom', old, old), ('Amplifier Unified', old+'/* my changes */', old+'/* my changes */')]:
        state = {'theme': {'name': name, 'css': css}}
        upgrade_default(state, new)
        assert state['theme'] == {'name': name, 'css': expected}
