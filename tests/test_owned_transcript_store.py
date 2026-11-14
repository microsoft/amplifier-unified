"""Host intent joins against the prospective Foundation lifetime owner."""
from contextlib import closing
from pathlib import Path

import pytest

from amplifier_web.host.storage import SessionStore


def test_explicit_rewrite_preserves_filtering_metadata_and_exact_owner(tmp_path):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    with closing(VersionedSessionBinding.create_new(tmp_path / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        store = SessionStore(tmp_path, canonical_binding=binding)
        before = binding.expected
        messages = [{'role': 'system', 'content': 'ephemeral instructions'},
                    {'role': 'user', 'content': 'hello'}]
        with pytest.raises(ValueError, match='explicit'):
            store.save('owned', messages, {})
        assert binding.expected == before
        saved = store.save('owned', messages, {'api_key': 'never-persist', 'bundle_name': 'work'},
                           expected=before, operation_id='checkpoint', admit=lambda: None)
        assert saved == [{'role': 'user', 'content': 'hello'}]
        loaded, metadata = store.load('owned')
        assert loaded == saved and metadata['bundle_name'] == 'work'
        assert 'api_key' not in metadata
        with pytest.raises(ValueError):
            store.save('other', messages, {}, expected=binding.expected,
                       operation_id='foreign', admit=lambda: None)
        with pytest.raises(ValueError):
            store.save('owned', messages, {}, expected=before,
                       operation_id='stale', admit=lambda: None)


def test_store_rejects_other_root_without_enabling_legacy_writer(tmp_path):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    with closing(VersionedSessionBinding.create_new(parent / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        with pytest.raises(ValueError, match='another'):
            SessionStore(tmp_path, canonical_binding=binding)
        with pytest.raises(ValueError):
            SessionStore(parent).save('owned', [], {})
        assert binding.load_messages() == []


@pytest.mark.parametrize('during_admit', [False, True])
def test_canonical_checkpoint_preserves_authoritative_naming(tmp_path, during_admit):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.metadata import SessionMetadataStore
    with closing(VersionedSessionBinding.create_new(tmp_path / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        store = SessionStore(tmp_path, canonical_binding=binding)
        metadata = SessionMetadataStore(binding.directory)
        metadata.update({'name': 'manual existing', 'name_source': 'manual', 'name_revision': 20,
                         'description': 'manual description'})
        def admission():
            if during_admit:
                metadata.update({'name': 'newer manual rename', 'name_source': 'manual', 'name_revision': 21,
                                 'description': 'new description'})
        store.save('owned', [{'role': 'user', 'content': 'hello'}],
                   {'name': 'stale runtime', 'name_source': 'automatic', 'name_revision': 1,
                    'description': 'stale description'},
                   expected=binding.expected, operation_id='checkpoint', admit=admission)
        current = metadata.read()
        assert current['name'] == ('newer manual rename' if during_admit else 'manual existing')
        assert current['name_revision'] == (21 if during_admit else 20)
        assert current['name_source'] == 'manual'
        assert current['description'] == ('new description' if during_admit else 'manual description')


@pytest.mark.parametrize('restore', [True, False])
def test_owned_bundle_transaction_uses_generations_not_directory_fence_overwrite(tmp_path, monkeypatch, restore):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.bundle_selection import BundleTransaction
    original_rows = [{'role': 'system', 'content': 'old instructions'},
                     {'role': 'user', 'content': 'retain user'}]
    with closing(VersionedSessionBinding.create_new(tmp_path / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        binding.rewrite(original_rows, expected=binding.expected, operation_id='seed', admit=lambda: None)
        expected = binding.expected
        monkeypatch.setattr(SessionStore, 'for_app', classmethod(
            lambda cls, home, workspace, **kw: cls(tmp_path, **kw)))
        home = tmp_path / 'app'
        local = home / 'sessions' / 'owned'
        local.mkdir(parents=True)
        (local / 'configuration.json').write_text('{"original":true}')
        (binding.directory / 'metadata.json').write_text('{"bundle":"old"}')
        transaction = BundleTransaction(home, tmp_path, 'owned', canonical_binding=binding)
        transaction.begin()
        transaction.select('new')
        assert binding.expected.generation != expected.generation
        assert binding.load_messages() == original_rows[1:]
        assert (binding.directory / 'transcript.jsonl').is_dir()
        assert (binding.directory / 'transcript.jsonl.backup').is_dir()
        assert not (local / 'configuration.json').exists()
        if restore:
            transaction.restore()
            assert binding.load_messages() == original_rows
            assert (local / 'configuration.json').read_text() == '{"original":true}'
            import json
            assert json.loads((binding.directory / 'metadata.json').read_text()) == {'bundle': 'old'}
        else:
            transaction.commit()
            assert binding.load_messages() == original_rows[1:]
        assert not transaction.path.exists()


def test_owned_bundle_uncertain_write_preserves_journal_and_never_replays(tmp_path, monkeypatch):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.bundle_selection import BundleTransaction
    with closing(VersionedSessionBinding.create_new(tmp_path / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        monkeypatch.setattr(SessionStore, 'for_app', classmethod(
            lambda cls, home, workspace, **kw: cls(tmp_path, **kw)))
        transaction = BundleTransaction(tmp_path / 'app', tmp_path, 'owned', canonical_binding=binding)
        transaction.begin()
        actual = binding.rewrite
        calls = []
        def lost(*args, **kwargs):
            calls.append(kwargs['operation_id'])
            actual(*args, **kwargs)
            raise OSError('lost result after publication')
        monkeypatch.setattr(binding, 'rewrite', lost)
        with pytest.raises(OSError):
            transaction.select('new')
        with pytest.raises(ValueError, match='uncertain'):
            transaction.restore()
        with pytest.raises(ValueError):
            transaction.select('another')
        assert len(calls) == 1 and transaction.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('late_error', [False, True])
async def test_owned_rewind_generations_and_unknown_exit_never_rollback_or_replay(tmp_path, monkeypatch, late_error):
    import copy
    import json
    from types import SimpleNamespace
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.history_revision import rewind, receipt_path, recover_pending
    from test_history_revision import source
    from test_session_fork import transcript
    home = tmp_path / 'app'
    parent = tmp_path / 'native'
    parent.mkdir(mode=0o700)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    with closing(VersionedSessionBinding.create_new(parent / 'edit-session', session_id='edit-session',
                                                   operation_id='init', admit=lambda: None)) as binding:
        store = SessionStore(parent, canonical_binding=binding)
        monkeypatch.setattr(SessionStore, 'for_app', classmethod(
            lambda cls, home, workspace, **kw: cls(parent, **kw)))
        src = source(tmp_path)
        class Controls:
            def __init__(self):
                self.rows = transcript()
                self.session = SimpleNamespace(session_id='edit-session')
                self.coordinator = SimpleNamespace(session_state={'goal': {'old': True}}, get=lambda name: self)
                self.calls = 0
            def require_idle(self):
                pass
            def state_path(self):
                return home / 'sessions' / 'edit-session' / 'control-state.json'
            async def get_messages(self):
                return copy.deepcopy(self.rows)
            async def set_messages(self, rows):
                self.rows = copy.deepcopy(rows)
            async def checkpoint(self):
                self.calls += 1
                store.save('edit-session', self.rows, {'preserve_system': True}, preserve_system=True,
                           expected=binding.expected, operation_id=f'checkpoint-{self.calls}', admit=lambda: None)
                if late_error and self.calls == 3:
                    raise OSError('lost checkpoint result after actual commit')
        controls = Controls()
        await controls.checkpoint()
        before = binding.expected
        args = {'source': src, 'messageId': '2', 'operationId': 'owned-edit'}
        if late_error:
            with pytest.raises(OSError):
                await rewind(controls, args, canonical_binding=binding)
            evidence = json.loads(receipt_path(home, 'edit-session', 'owned-edit').read_text())
            assert evidence['phase'] == 'prepared' and controls.calls == 3
            assert binding.load_messages() == transcript()[:6]
            # Recovery classifies the retained operation, never writes a new one.
            recover_pending(home, tmp_path, 'edit-session', canonical_binding=binding)
            assert json.loads(receipt_path(home, 'edit-session', 'owned-edit').read_text())['phase'] == 'committed'
            assert controls.calls == 3
        else:
            result = await rewind(controls, args, canonical_binding=binding)
            assert result['nativeRevision'] is not None
            assert binding.load_messages() == transcript()[:6]
            assert binding.expected.generation != before.generation
            assert json.loads(receipt_path(home, 'edit-session', 'owned-edit').read_text())['phase'] == 'committed'
        assert (binding.directory / 'transcript.jsonl').is_dir()
        assert (binding.directory / 'transcript.jsonl.backup').is_dir()


def test_owned_bundle_restore_preserves_newer_manual_naming(tmp_path, monkeypatch):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from amplifier_web.bundle_selection import BundleTransaction
    with closing(VersionedSessionBinding.create_new(tmp_path / 'owned', session_id='owned',
                                                   operation_id='init', admit=lambda: None)) as binding:
        monkeypatch.setattr(SessionStore, 'for_app', classmethod(
            lambda cls, home, workspace, **kw: cls(tmp_path, **kw)))
        metadata = SessionMetadataStore(binding.directory)
        metadata.update({'name': 'A', 'name_source': 'manual', 'name_revision': 1, 'bundle': 'old'})
        transition = BundleTransaction(tmp_path / 'app', tmp_path, 'owned', canonical_binding=binding)
        transition.begin()
        # Also cover a naming change between begin and selection.
        metadata.update({'name': 'B', 'name_source': 'manual', 'name_revision': 2})
        transition.select('new')
        assert metadata.read()['name'] == 'B'
        metadata.update({'name': 'C', 'name_source': 'manual', 'name_revision': 3})
        transition.restore()
        current = metadata.read()
        assert current['name'] == 'C' and current['name_revision'] == 3 and current['name_source'] == 'manual'
        assert current['bundle'] == 'old' and 'bundle_name' not in current