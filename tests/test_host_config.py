"""Standalone settings migration and loop policy contracts."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from amplifier_web.host.config import _KEY_FILE_VALUES, _load_keys, app_home, load_config, prepare_registry, merge, expand_environment
from amplifier_web.host.session import live_plan, repair_interrupted_receipts, redact, _apply_settings
from amplifier_web.shared_state import configuration_paths, workspace_snapshot_path


class HostSettingsTests(unittest.TestCase):
    def test_app_home_honors_the_data_directory_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"AMPLIFIER_WEB_DATA_DIR": directory}, clear=True):
            self.assertEqual(app_home(), Path(directory).resolve())

    def test_configuration_invalidation_tracks_shared_inputs_without_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            legacy = root / "legacy"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".amplifier").mkdir()

            load_config(workspace, home=home, legacy_home=legacy)

            snapshot = workspace_snapshot_path(workspace, home)
            self.assertFalse(snapshot.exists())
            paths = configuration_paths(workspace, "session-id", home)
            self.assertIn(Path(os.environ["AMPLIFIER_HOME"]) / "settings.yaml", paths)
            self.assertIn(workspace.resolve() / ".amplifier" / "settings.local.yaml", paths)
            self.assertNotIn(snapshot, paths)

    def test_workspace_snapshot_is_stable_through_symlink_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            workspace=root/'workspace';workspace.mkdir()
            alias=root/'alias';alias.symlink_to(workspace,target_is_directory=True)
            home=root/'home';home.mkdir()
            home_alias=root/'home-alias';home_alias.symlink_to(home,target_is_directory=True)
            self.assertEqual(workspace_snapshot_path(alias,home_alias),workspace_snapshot_path(workspace,home))

    def test_keys_file_refreshes_a_value_it_previously_loaded(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "keys.env"
            path.write_text("AMPLIFIER_WEB_REFRESH_TEST=first\n")
            _KEY_FILE_VALUES.clear()
            _load_keys(path)
            path.write_text("AMPLIFIER_WEB_REFRESH_TEST=second\n")
            _load_keys(path)
            self.assertEqual(os.environ["AMPLIFIER_WEB_REFRESH_TEST"], "second")
            _KEY_FILE_VALUES.clear()

    def test_shared_settings_reload_while_runtime_cache_remains_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);legacy=root/'legacy';home=root/'owned';workspace=root/'workspace'
            legacy.mkdir();workspace.mkdir();(workspace/'.amplifier').mkdir()
            (legacy/'settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':'custom'},'config':{'providers':[{'module':'provider-test','id':'first','config':{'model':'a'}}]}}))
            (legacy/'keys.env').write_text('AMPLIFIER_MIGRATION_TEST_TOKEN="example test credential"\n')
            (legacy/'cache/module').mkdir(parents=True);(legacy/'cache/module/source.py').write_text('pass')
            (legacy/'registry.json').write_text(json.dumps({'version':1,'bundles':{'custom':{'uri':'git+https://example.org/custom','local_path':str(legacy/'cache/module')}}}))
            (workspace/'.amplifier/settings.yaml').write_text(yaml.safe_dump({'config':{'providers':[{'module':'provider-test','id':'first','config':{'model':'b'}},{'module':'provider-test','id':'second','config':{'model':'c'}}]}}))
            with patch.dict(os.environ, {}, clear=False):
                config=load_config(workspace,home=home,legacy_home=legacy)
                self.assertEqual(config.active_bundle,'custom')
                self.assertEqual([r['config']['model'] for r in config.providers],['b','c'])
                self.assertEqual(os.environ['AMPLIFIER_MIGRATION_TEST_TOKEN'],'example test credential')
                self.assertFalse((home/'config/keys.env').exists())
                self.assertFalse((home/'foundation').exists())
                prepare_registry(config)
                owned=json.loads((home/'foundation/registry.json').read_text())
                self.assertEqual(owned['bundles']['custom']['local_path'],str((home/'foundation/cache/module').resolve()))
                (legacy/'settings.yaml').write_text('bundle:\n  active: changed-externally\n')
                again=load_config(workspace,home=home,legacy_home=legacy)
                self.assertEqual(again.active_bundle,'changed-externally')

    def test_provider_instances_merge_by_instance_not_module(self):
        result=merge([{'module':'p','id':'a','config':{'secret':'retained','model':'old'}},{'module':'p','id':'b'}],
                     [{'module':'p','id':'a','config':{'model':'new'}}])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['config'],{'secret':'retained','model':'new'})

    def test_legacy_private_settings_do_not_override_shared_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';workspace=root/'project';legacy=root/'legacy'
            (home/'config').mkdir(parents=True);workspace.mkdir();(workspace/'.amplifier').mkdir()
            (home/'config/settings.yaml').write_text(yaml.safe_dump({'bundle':{'app':['added']},'web_bundles':{
                'entries':[{'uri':'added','role':'behavior','enabled':True},{'uri':'disabled','role':'behavior','enabled':False}],
                'excluded':['removed']}}))
            (workspace/'.amplifier/settings.yaml').write_text(yaml.safe_dump({'bundle':{'app':['disabled','removed','project-only']}}))
            (workspace/'.amplifier-unified').mkdir()
            (workspace/'.amplifier-unified/settings.local.yaml').write_text('local_setting: active\n')
            result=load_config(workspace,home=home,legacy_home=legacy)
            self.assertEqual(result.app_bundles,['disabled','removed','project-only'])
            self.assertNotIn('local_setting',result.settings)

    def test_legacy_provider_ids_map_to_core_instances_without_collapsing(self):
        bundle=SimpleNamespace(providers=[{'module':'p','instance_id':'one','config':{'model':'old'}}],tools=[],hooks=[],session={})
        providers=[{'module':'p','id':'one','config':{'model':'new'}},{'module':'p','id':'two'}]
        config=SimpleNamespace(settings={'config':{'providers':providers}},providers=providers)
        result=_apply_settings(bundle,config)
        self.assertEqual([p['instance_id'] for p in result.providers],['one','two'])
        self.assertEqual(result.providers[0]['config']['model'],'new')

    def test_environment_values_never_execute_shell_and_missing_is_explicit(self):
        with patch.dict(os.environ,{'AMPLIFIER_TEST_VALUE':'$(echo private)','AMPLIFIER_TEST_EMPTY':''},clear=False):
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_VALUE}'),'$(echo private)')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_VALUE}',environment={'AMPLIFIER_TEST_VALUE':'injected'}),'injected')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:-fallback}'),'fallback')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:INFO}'),'INFO')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_ABSENT:}'),'')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_EMPTY:-fallback}'),'fallback')
            self.assertEqual(expand_environment('${AMPLIFIER_TEST_EMPTY:fallback}'),'')
            with self.assertRaisesRegex(ValueError,'AMPLIFIER_TEST_ABSENT'):
                expand_environment('${AMPLIFIER_TEST_ABSENT}')

    def test_apply_settings_defers_provider_config_but_expands_provider_source(self):
        bundle=SimpleNamespace(providers=[],tools=[],hooks=[],session={})
        providers=[{'module':'provider-test','id':'one','source':'${PROVIDER_SOURCE}','config':{'base_url':'${OPTIONAL_BASE_URL}'}}]
        config=SimpleNamespace(settings={'config':{'providers':providers}},providers=providers)
        with patch.dict(os.environ,{'PROVIDER_SOURCE':'source'},clear=False):
            result=_apply_settings(bundle,config)
            self.assertEqual(result.providers[0]['source'],'source')
            self.assertEqual(result.providers[0]['config']['base_url'],'${OPTIONAL_BASE_URL}')

    def test_unsupported_root_orchestrator_is_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError,'not compatible'):
            live_plan({'session':{'orchestrator':{'module':'custom-loop'}}})

    def test_loop_overlay_preserves_baseline_and_custom_finite_agent(self):
        original={'session':{'orchestrator':{'module':'loop-streaming','config':{'max_turns':10}}},
                  'agents':{'worker':{'session':{'orchestrator':{'module':'custom-finite'}}}}}
        adapted,replacements=live_plan(original)
        self.assertEqual(original['session']['orchestrator']['module'],'loop-streaming')
        self.assertEqual(adapted['session']['orchestrator']['module'],'loop-live')
        self.assertEqual(adapted['agents']['worker']['session']['orchestrator']['module'],'custom-finite')
        self.assertTrue(replacements)

    def test_live_plan_preserves_opt_in_background_policy(self):
        adapted, _ = live_plan({'session': {'orchestrator': {'module': 'loop-live', 'config': {'background_delegate': False}}}})
        self.assertFalse(adapted['session']['orchestrator']['config']['background_delegate'])

    def test_imported_queued_receipt_is_history_not_replay(self):
        rows=[{'role':'tool','tool_call_id':'c','content':json.dumps({'status':'queued','call_id':'c','job_id':'j'})}]
        repaired=repair_interrupted_receipts(rows)
        self.assertEqual(json.loads(repaired[0]['content'])['status'],'interrupted')
        self.assertEqual(json.loads(rows[0]['content'])['status'],'queued')

    def test_report_redacts_nested_credentials(self):
        value=redact({'providers':[{'config':{'api_key':'private','default_model':'model'}}]})
        self.assertNotIn('private',json.dumps(value))
        self.assertEqual(value['providers'][0]['config']['default_model'],'model')


if __name__=='__main__':unittest.main()
