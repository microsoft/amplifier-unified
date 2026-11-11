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


def test_exact_stock_preset_upgrade_preserves_custom_edits(monkeypatch):
    from amplifier_web import default_typography
    from amplifier_web.theme_library import preset
    from types import SimpleNamespace
    old = '#amp-one{--a-radius:24px} /* exact historical preset fixture */'
    monkeypatch.setattr(default_typography, 'PREVIOUS_PRESETS', {hashlib.sha256(old.encode()).hexdigest(): 'graphite'})
    new = '#amp-one{--a-radius:24px} /* current base */'
    state = {'theme': {'name': 'Graphite', 'css': old}}
    upgrade_default(state, new)
    assert state['theme']['css'] == preset(SimpleNamespace(default_theme=lambda: new), 'graphite')['css']
    custom = {'theme': {'name': 'Graphite', 'css': old + '\n/* my edit */'}}
    upgrade_default(custom, new)
    assert custom['theme']['css'].endswith('/* my edit */')
