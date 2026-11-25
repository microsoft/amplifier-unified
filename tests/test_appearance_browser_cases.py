"""Public appearance inputs for the named browser scenario cannot silently drift."""
import hashlib
import json
from pathlib import Path

from amplifier_web import theme_library


ROOT = Path(__file__).resolve().parents[1]


def test_browser_appearance_cases_match_every_current_public_theme_read():
    fixture = json.loads((ROOT / 'tests/fixtures/appearance-cases.json').read_text())
    rows = fixture['appearances']
    assert [row['id'] for row in rows] == ['reference', *(
        'builtin:' + name for name in theme_library.BUILTINS)]
    assert rows[0] == {'id': 'reference', 'name': 'Reference', 'css': ''}
    for row in rows[1:]:
        actual = theme_library.read(None, row['id'])
        # theme.read is public and pure for bundled IDs. Compare actual generated
        # CSS and metadata, not an incomplete appearances-directory fallback.
        assert {key: row[key] for key in actual} == actual
        assert row['sha256'] == hashlib.sha256(actual['css'].encode()).hexdigest()


def test_settings_action_spacing_owns_the_tracked_public_appearance_fixture():
    selection = json.loads((ROOT / 'components/web-client.source.json').read_text())
    client = ROOT / (selection['delivery']['path'] if selection['delivery']['type'] == 'directory'
                     else '.build/web-client')
    plan = json.loads((client / 'tests/browser-checks.json').read_text())
    owned = [row for row in plan['scenarios'] if row['id'] == 'settings-action-spacing'
             or 'tests/settings-action-spacing-browser.mjs' in row['command']]
    assert owned == [{
        'id': 'settings-action-spacing',
        'command': ['node', 'tests/settings-action-spacing-browser.mjs'],
        'env': {
            'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/settings-action-spacing',
            'AMPLIFIER_TEST_APPEARANCES': '{root}/tests/fixtures/appearance-cases.json',
        },
    }]
    host = json.loads((ROOT / 'tests/web/browser-checks.json').read_text())
    assert host['scenarioOrder'].count('settings-action-spacing') == 1
