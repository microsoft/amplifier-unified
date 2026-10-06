"""An ordinary work/ directory must not replace the registered Work bundle."""
import pytest
from amplifier_foundation.exceptions import BundleLoadError
from amplifier_web.host.bundle_paths import canonical_bundle_reference, local_bundle_path
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import load_root_bundle


@pytest.fixture
def configured(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'work').mkdir()
    registered = tmp_path / 'registered.yaml'
    registered.write_text('bundle:\n  name: registered-work\n  version: 1.0.0\n')
    return HostConfig(tmp_path / 'app', workspace,
        {'bundle': {'app': [], 'added': {'work': str(registered)}}}, tmp_path / 'registry')


async def test_registered_work_loads_despite_unrelated_workspace_directory(configured):
    assert local_bundle_path(configured, 'work') is None
    _, loaded, chosen = await load_root_bundle(configured, 'work')
    assert chosen == 'work'
    assert loaded.name == 'registered-work'


@pytest.mark.parametrize('reference', ['./work', 'work/', 'absolute', 'uri'])
async def test_explicit_invalid_local_directory_fails_at_requested_path(configured, reference):
    path = configured.workspace / 'work'
    reference = str(path) if reference == 'absolute' else path.as_uri() if reference == 'uri' else reference
    assert local_bundle_path(configured, reference) == path
    with pytest.raises(BundleLoadError, match='Not a valid bundle'):
        await load_root_bundle(configured, reference)


@pytest.mark.parametrize('manifest', ['bundle.md', 'bundle.yaml', 'bundle.yml'])
def test_bare_local_bundle_directory_keeps_its_existing_precedence(configured, manifest):
    path = configured.workspace / 'work'
    (path / manifest).write_text('bundle:\n  name: local-work\n')
    assert local_bundle_path(configured, 'work') == path


def test_invalid_workspace_candidate_does_not_hide_valid_user_bundle(configured):
    local = configured.home / 'bundles/work'
    local.mkdir(parents=True)
    (local / 'bundle.yaml').write_text('bundle:\n  name: user-work\n')
    assert local_bundle_path(configured, 'work') == local


def test_unrelated_bare_file_does_not_shadow_alias_but_explicit_file_is_preserved(configured):
    path = configured.workspace / 'anchors'
    path.write_text('ordinary document')
    assert local_bundle_path(configured, 'anchors') is None
    assert local_bundle_path(configured, './anchors') == path
    yaml = configured.workspace / 'custom.yaml'
    yaml.write_text('bundle:\n  name: custom\n')
    assert local_bundle_path(configured, 'custom.yaml') == yaml


def test_remote_registration_remains_a_registry_reference(configured):
    assert local_bundle_path(configured, 'git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=bundle.md') is None


@pytest.mark.parametrize('reference', ['Work', 'WORK', 'wOrK'])
async def test_registered_ids_accept_case_without_rewriting_the_source(configured, reference):
    assert canonical_bundle_reference(configured, reference) == 'work'
    _, loaded, chosen = await load_root_bundle(configured, reference)
    assert chosen == 'work' and loaded.name == 'registered-work'


def test_exact_alias_wins_but_ambiguous_case_fallback_fails(configured):
    configured.settings['bundle']['added']['Work'] = 'custom-source'
    assert canonical_bundle_reference(configured, 'Work') == 'Work'
    assert canonical_bundle_reference(configured, 'work') == 'work'
    with pytest.raises(ValueError, match='Ambiguous bundle ID'):
        canonical_bundle_reference(configured, 'WORK')


@pytest.mark.parametrize('reference', ['./Work', '/Work', 'file:///Work',
    'git+https://example.invalid/Work@MAIN', 'https://example.invalid/Work',
    'custom.yaml', 'unknown-id'])
def test_paths_urls_and_unknown_ids_are_not_lowercased(configured, reference):
    assert canonical_bundle_reference(configured, reference) == reference


def test_real_local_bundle_with_display_casing_keeps_precedence(configured):
    local = configured.workspace / 'Work'
    local.mkdir()
    (local / 'bundle.yaml').write_text('bundle:\n  name: local\n')
    assert canonical_bundle_reference(configured, 'Work') == 'Work'
