"""Cheap source/storage checks for appearance profiles, not browser proof.

Runs with unittest and existing jsonschema; no app runtime or private data.
The paired runtime/storage/browser tests remain in test_appearance_preferences.
"""
import ast
import copy
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest

from amplifier_web.shell_modules import ShellModules


ROOT = Path(__file__).resolve().parents[1]


class ExperimentStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE shell_records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))')
        self.shell = ShellModules.__new__(ShellModules)
        self.shell.db = self.db

    def tearDown(self):
        self.db.close()

    def seed(self, kind, identity, value):
        import json
        self.db.execute('INSERT INTO shell_records VALUES (?,?,?)', (kind, identity, json.dumps(value)))
        self.db.commit()

    def test_legacy_shell_presentation_is_inert_and_never_overwritten(self):
        old = {'revision': 7, 'composition': {'instances': [], 'presentation': {'scheme': 'dark'}},
               'lastGood': {'instances': [], 'presentation': {'scheme': 'dark'}},
               'views': {}, 'preview': {'id': 'old-preview'},
               'preferenceCommand': {'id': 'old-pending-command'}}
        self.seed('client', 'browser', old)
        before = self.db.execute("SELECT value FROM shell_records WHERE kind='client'").fetchone()[0]
        self.assertIsNone(self.shell.get('client', 'browser'))
        self.shell.put('client', 'browser', {'revision': 8})
        self.assertEqual(self.shell.get('client', 'browser')['revision'], 8)
        self.assertEqual(self.db.execute("SELECT value FROM shell_records WHERE kind='client'").fetchone()[0], before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shell_records WHERE kind='appearance-client-v2'").fetchone()[0], 1)

    def test_nested_transaction_failure_retains_old_records_and_no_receipt(self):
        old = {'revision': 1}
        self.shell.put('client', 'browser', old)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with self.shell.transaction():
                self.shell.put('client', 'browser', {'revision': 2})
                with self.shell.transaction():
                    self.shell.put('command', 'effect', {'status': 'stored'})
                raise RuntimeError('injected')
        self.assertEqual(self.shell.get('client', 'browser'), old)
        self.assertIsNone(self.shell.get('command', 'effect'))

    def test_notifications_are_after_atomic_commit(self):
        observed = []
        self.shell.notify = lambda identity: observed.append((identity, self.shell.get('client', identity)))
        with self.shell.transaction():
            self.shell.put('client', 'browser', {'revision': 2})
            self.shell._pending_notifications = {'browser'}
            self.assertEqual(observed, [])
        self.assertEqual(observed, [('browser', {'revision': 2})])


class ExperimentSourceTests(unittest.TestCase):
    def test_startup_does_not_delete_or_upgrade_legacy_theme(self):
        tree = ast.parse((ROOT / 'amplifier_web/service.py').read_text())
        owner = next(row for row in tree.body if isinstance(row, ast.ClassDef) and row.name == 'AppService')
        startup = next(row for row in owner.body if isinstance(row, ast.FunctionDef) and row.name == '__init__')
        for call in (row for row in ast.walk(startup) if isinstance(row, ast.Call)):
            if isinstance(call.func, ast.Name):
                self.assertNotEqual(call.func.id, 'upgrade_default')
            if isinstance(call.func, ast.Attribute) and call.func.attr in {'pop', 'setdefault'}:
                self.assertFalse(call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == 'theme')

    def test_experiment_profile_does_not_reuse_retained_profile_key(self):
        source = (ROOT / 'amplifier_web/appearance_preferences.py').read_text()
        tree = ast.parse(source)
        constants = {row.targets[0].id: ast.literal_eval(row.value) for row in tree.body
                     if isinstance(row, ast.Assign) and isinstance(row.targets[0], ast.Name)
                     and row.targets[0].id in {'PREFERENCES_KEY', 'DEFAULTS'}}
        self.assertEqual(constants['PREFERENCES_KEY'], 'shared-reference-v2')
        self.assertEqual(constants['DEFAULTS']['appearanceId'], 'builtin:default')
        self.assertNotIn('AMPLIFIER_APPEARANCE_EXPERIMENT', source)
        for call in (row for row in ast.walk(tree) if isinstance(row, ast.Call)):
            if isinstance(call.func, ast.Attribute) and call.func.attr == 'put' and call.args:
                self.assertFalse(len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
                                 and call.args[1].value in {'reference-v1', 'shared-reference-v1', 'appearance'})

    def test_seven_builtin_deltas_are_separate_from_client_structural_rules(self):
        for name in ('default', 'aurora', 'atelier', 'graphite', 'digital-rain', 'lightline', 'ready'):
            css = (ROOT / 'amplifier_web/appearances' / (name + '.amplifier.css')).read_text()
            self.assertIn('light-dark(', css, name)
            self.assertNotIn('position:fixed', css.replace(' ', ''), name)


class AccentProfileTests(unittest.TestCase):
    """Execute the real pure profile/schema functions without app dependencies."""
    def setUp(self):
        from jsonschema import validate
        from amplifier_web.action_schema import schema, string
        source = ast.parse((ROOT / 'amplifier_web/appearance_preferences.py').read_text())
        selected = [row for row in source.body
                    if isinstance(row, ast.FunctionDef) and row.name in {'compose_values', 'patch_values', 'fields', 'definitions'}
                    or isinstance(row, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DEFAULTS'
                                                           for t in row.targets)]
        namespace = {'copy': copy, '__name__': 'amplifier_web.appearance_preferences',
                     '__package__': 'amplifier_web',
                     'theme_library': SimpleNamespace(LIMIT=1_000_000)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), '<profile-functions>', 'exec'), namespace)
        self.compose = namespace['compose_values']
        self.patch = namespace['patch_values']
        self.defaults = namespace['DEFAULTS']
        self.definitions = namespace['definitions'](schema, string)
        self.validate = validate

    def test_different_appearance_drops_only_old_accent(self):
        original = {**self.defaults, 'accent': '#dd44aa', 'density': 'compact',
                    'executionVisibility': {'turn': False}}
        before = copy.deepcopy(original)
        result = self.patch(original, {'appearanceId': 'builtin:digital-rain'})
        self.assertNotIn('accent', result)
        self.assertEqual(result['appearanceId'], 'builtin:digital-rain')
        self.assertEqual(result['density'], 'compact')
        self.assertEqual(result['executionVisibility'], {'turn': False})
        self.assertEqual(original, before)

    def test_same_appearance_and_unrelated_edits_retain_override(self):
        original = {**self.defaults, 'accent': '#dd44aa'}
        for patch in ({'appearanceId': original['appearanceId']}, {'scheme': 'dark'}):
            self.assertEqual(self.patch(original, patch)['accent'], '#dd44aa')

    def test_explicit_new_color_applies_to_new_appearance(self):
        result = self.patch({**self.defaults, 'accent': '#dd44aa'},
                              {'appearanceId': 'builtin:graphite', 'accent': '#123456'})
        self.assertEqual(result['accent'], '#123456')

    def test_reset_deletes_override_without_palette_or_profile_replacement(self):
        original = {**self.defaults, 'appearanceId': 'builtin:digital-rain', 'accent': '#dd44aa',
                    'scheme': 'dark', 'decorations': False}
        result = self.patch(original, {'accent': None})
        self.assertEqual(result, {key: value for key, value in original.items() if key != 'accent'})
        self.assertEqual(self.patch(result, {'accent': None}), result)

    def test_independent_complete_profile_keeps_its_own_explicit_accent(self):
        local = {**self.defaults, 'appearanceId': 'builtin:lightline', 'accent': '#123456'}
        self.assertEqual(self.compose(local), local)
        other = {**self.defaults, 'appearanceId': 'builtin:digital-rain', 'accent': '#dd44aa'}
        self.assertEqual(self.compose(local, other), other)

    def test_null_is_an_update_operation_not_reportable_storage(self):
        update = self.definitions['appearance.preferences.update'][1]
        self.validate({'scope': 'shared', 'expectedRevision': 0, 'patch': {'accent': None}}, update)
        report = {'generation': 'one', 'revision': 0, 'activeScope': 'client', 'storageVersion': 'one',
                  'status': 'ready', 'values': {**self.defaults, 'accent': None}}
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):
            self.validate(report, self.definitions['appearance.client.report'][1])
        report['values'].pop('accent')
        self.validate(report, self.definitions['appearance.client.report'][1])


class MixedActionAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE shell_records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))')
        self.shell = ShellModules.__new__(ShellModules)
        self.shell.db = self.db
        self.record = {'view': {'scheme': 'system'}, 'drafts': {'chat': 'keep'}}
        self.mirrors = {'one': {'revision': 0}}
        self.shell.service = SimpleNamespace(
            clients=SimpleNamespace(current=SimpleNamespace(get=lambda: 'one'), records={'one': self.record}),
            appearance=SimpleNamespace(mirrors=self.mirrors), state={'selectedSessionId': 'chat'},
            _state={'canvasArtifacts': []})
        self.lock = asyncio.Lock()
        self.observed = []
        self.shell.notify = lambda identity: self.observed.append(identity)

    async def asyncTearDown(self):
        self.db.close()

    async def test_outer_failure_rolls_back_command_and_private_edit_without_notification(self):
        with self.assertRaisesRegex(RuntimeError, 'receipt write'):
            async with self.shell.action_lock(self.lock, 'view.update', {'patch': {'scheme': 'dark'}}):
                with self.shell.transaction():
                    self.shell.put('client', 'one', {'preferenceCommand': {'id': 'pending'}})
                    self.shell._pending_notifications = {'one'}
                self.assertTrue(self.db.in_transaction)
                self.assertEqual(self.observed, [])
                self.record['drafts']['chat'] = 'must not survive'
                self.mirrors['one']['revision'] = 1
                raise RuntimeError('receipt write')
        self.assertIsNone(self.shell.get('client', 'one'))
        self.assertEqual(self.record['drafts']['chat'], 'keep')
        self.assertEqual(self.mirrors['one'], {'revision': 0})
        self.assertEqual(self.observed, [])

    async def test_notification_follows_enclosing_receipt_commit(self):
        async with self.shell.action_lock(self.lock, 'view.update', {'patch': {'layout': 'work'}}):
            with self.shell.transaction():
                self.shell.put('client', 'one', {'preferenceCommand': {'id': 'pending'}})
                self.shell._pending_notifications = {'one'}
            self.shell.put('command', 'outer', {'accepted': True})
            self.assertEqual(self.observed, [])
            self.db.commit()  # The service publisher's actual owning commit.
        self.assertEqual(self.shell.get('command', 'outer'), {'accepted': True})
        self.assertEqual(self.observed, ['one'])

    async def test_publisher_rollback_before_raise_still_restores_private_state(self):
        with self.assertRaisesRegex(RuntimeError, 'publisher rollback'):
            async with self.shell.action_lock(self.lock, 'view.update', {'patch': {'scheme': 'dark'}}):
                with self.shell.transaction():
                    self.shell.put('client', 'one', {'preferenceCommand': {'id': 'pending'}})
                    self.shell._pending_notifications = {'one'}
                self.record['view']['scheme'] = 'dark'
                self.record['drafts']['chat'] = 'must not survive'
                self.mirrors['one']['revision'] = 2
                self.db.rollback()  # Real _save() rolls back before re-raising.
                raise RuntimeError('publisher rollback')
        self.assertIsNone(self.shell.get('client', 'one'))
        self.assertEqual(self.record['view'], {'scheme': 'system'})
        self.assertEqual(self.record['drafts']['chat'], 'keep')
        self.assertEqual(self.mirrors['one'], {'revision': 0})
        self.assertEqual(self.shell._pending_notifications, set())
        self.assertEqual(self.observed, [])

    async def test_prior_progress_commit_is_not_mixed_action_acceptance(self):
        with self.assertRaisesRegex(RuntimeError, 'late rollback'):
            async with self.shell.action_lock(self.lock, 'view.update', {'patch': {'scheme': 'dark'}}, 'outer-id'):
                self.db.commit()  # A progress flush before this action.
                self.shell.service._saved_revision = 999
                self.db.execute('BEGIN')
                self.shell.put('client', 'one', {'preferenceCommand': {'id': 'pending'}})
                self.shell.stage_action_receipt()
                self.record['view']['scheme'] = 'dark'
                self.mirrors['one']['revision'] = 3
                self.db.rollback()
                raise RuntimeError('late rollback')
        self.assertEqual(self.record['view']['scheme'], 'system')
        self.assertEqual(self.mirrors['one']['revision'], 0)
        self.assertIsNone(self.shell.get('client', 'one'))

    async def test_committed_receipt_is_not_undone_by_readback_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'readback'):
            async with self.shell.action_lock(self.lock, 'view.update', {'patch': {'scheme': 'dark'}}):
                self.record['view']['scheme'] = 'dark'
                self.shell.put('client', 'one', {'preferenceCommand': {'id': 'accepted'}})
                self.shell.stage_action_receipt()
                self.db.commit()
                raise RuntimeError('readback')
        self.assertEqual(self.record['view']['scheme'], 'dark')
        self.assertEqual(self.shell.get('client', 'one')['preferenceCommand']['id'], 'accepted')

    async def test_canvas_preference_unit_after_progress_commit_stays_in_outer_transaction(self):
        surface = {'id': 'surface', 'app': {'requests': [{'status': 'pending'}]}}
        self.shell.service._state['canvasArtifacts'] = [surface]
        with self.assertRaisesRegex(RuntimeError, 'canvas publisher rollback'):
            async with self.shell.action_lock(self.lock, 'canvas.apps.resolve',
                                             {'id': 'surface', 'approve': True}, 'canvas-approval'):
                # A valid post-flush CAS can proceed after the admission barrier
                # commits progress. There is no presentation_patch helper on the
                # Canvas path to reopen the enclosing transaction.
                self.db.commit()
                with self.shell.transaction():
                    self.shell.put('preferences', 'experiment', {'scheme': 'dark'})
                    self.shell.put('client', 'one', {'preferenceCommand': {'id': 'queued'}})
                self.record['view']['scheme'] = 'dark'
                self.mirrors['one']['revision'] = 1
                surface['app']['requests'][0]['status'] = 'accepted'
                self.shell._pending_notifications = {'one'}
                self.shell.stage_action_receipt()
                self.db.rollback()
                raise RuntimeError('canvas publisher rollback')
        self.assertIsNone(self.shell.get('preferences', 'experiment'))
        self.assertIsNone(self.shell.get('client', 'one'))
        self.assertIsNone(self.shell.get('appearance-receipt', 'last-mixed-commit'))
        self.assertEqual(surface['app']['requests'], [{'status': 'pending'}])
        self.assertEqual(self.record['view'], {'scheme': 'system'})
        self.assertEqual(self.mirrors['one'], {'revision': 0})
        self.assertEqual(self.shell._pending_notifications, set())
        self.assertEqual(self.observed, [])


if __name__ == '__main__':
    unittest.main()