"""Only PEP 610's local-directory editable default is equivalent evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment, runtime_qualification


@pytest.fixture
def compare_graphs(tmp_path, monkeypatch):
    """Exercise both qualification paths without running an installer."""
    prepared = tmp_path / 'prepared'
    prepared.mkdir()
    (prepared / 'uv.lock').write_text('version = 1\n')
    manifest = tmp_path / 'base.toml'
    manifest.write_text('[project]\nname = "fixture-runtime"\nversion = "0.0.0"\n')
    monkeypatch.setattr(runtime_environment, 'manifest_path', lambda: manifest)
    generation = 'a' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, generation)
    receipt.mkdir(parents=True)

    class Diagnostics:
        async def run(self, phase, *args, **kwargs):
            assert phase in {'ecosystem-runtime-freeze', 'ecosystem-runtime-freeze-install',
                             'ecosystem-runtime-policy'}

    async def compare(expected, actual, mode, **kwargs):
        before_expected, before_actual = deepcopy(expected), deepcopy(actual)
        evidence = receipt / 'runtime-installed.json'
        before_receipt = None
        if mode == 'recorded':
            evidence.write_text(json.dumps(expected, indent=2) + '\n')
            before_receipt = evidence.read_bytes()
        monkeypatch.setattr(runtime_qualification, 'installed_graph',
                            lambda project: expected if mode == 'freeze' and Path(project) == prepared else actual)
        try:
            if mode == 'freeze':
                manager = SimpleNamespace(home=tmp_path, diagnostics=Diagnostics())
                await runtime_qualification.freeze(manager, generation, prepared)
                # The receipt keeps the installer's raw metadata, not the comparison view.
                assert json.loads(evidence.read_text()) == actual
            else:
                runtime_qualification.verify_recorded(prepared, receipt, **kwargs)
        finally:
            assert expected == before_expected and actual == before_actual
            if before_receipt is not None:
                assert evidence.read_bytes() == before_receipt

    return compare


def local_record():
    return {'name': 'amplifier-local', 'version': '1.0', 'path': '/qualified/cache/module',
            'directUrl': {'url': 'file:///qualified/cache/module', 'dir_info': {}},
            'cacheSource': {'url': 'https://example.invalid/source', 'ref': 'main',
                            'revision': 'a' * 40, 'subdirectory': 'module', 'dirty': False,
                            'trackedContentSha256': 'b' * 64}}


@pytest.mark.parametrize('mode', ['freeze', 'recorded'])
@pytest.mark.parametrize('explicit_first', [False, True])
async def test_missing_editable_equals_false_without_rewriting_evidence(compare_graphs, mode, explicit_first):
    missing, explicit = local_record(), local_record()
    explicit['directUrl']['dir_info']['editable'] = False
    expected, actual = (explicit, missing) if explicit_first else (missing, explicit)
    await compare_graphs([expected], [actual], mode)


@pytest.mark.parametrize('mode', ['freeze', 'recorded'])
@pytest.mark.parametrize(('path', 'value'), [
    (('directUrl', 'dir_info', 'editable'), True),
    (('directUrl', 'dir_info', 'editable'), 0),
    (('directUrl', 'dir_info', 'editable'), None),
    (('directUrl', 'dir_info', 'editable'), 'false'),
    (('directUrl', 'dir_info'), None),
    (('directUrl', 'dir_info', 'other'), 'different'),
    (('directUrl', 'url'), 'file:///different/source'),
    (('directUrl', 'subdirectory'), 'different'),
    (('path',), '/different/source'),
    (('version',), '2.0'),
    (('cacheSource', 'url'), 'https://example.invalid/different'),
    (('cacheSource', 'ref'), 'different'),
    (('cacheSource', 'revision'), 'c' * 40),
    (('cacheSource', 'subdirectory'), 'different'),
    (('cacheSource', 'dirty'), True),
    (('cacheSource', 'trackedContentSha256'), 'd' * 64),
])
async def test_local_provenance_changes_still_fail(compare_graphs, mode, path, value):
    expected, actual = local_record(), local_record()
    expected['directUrl']['dir_info']['editable'] = False
    target = actual
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match='differs from|changed after'):
        await compare_graphs([expected], [actual], mode)


@pytest.mark.parametrize('mode', ['freeze', 'recorded'])
@pytest.mark.parametrize('field', ['url', 'hashes'])
async def test_archive_source_and_hash_stay_exact(compare_graphs, mode, field):
    expected = {'name': 'amplifier-archive', 'version': '1.0', 'directUrl': {
        'url': 'https://example.invalid/source.tar.gz', 'archive_info': {'hashes': {'sha256': 'a' * 64}}}}
    actual = deepcopy(expected)
    if field == 'url':
        actual['directUrl']['url'] = 'https://example.invalid/other.tar.gz'
    else:
        actual['directUrl']['archive_info']['hashes']['sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='differs from|changed after'):
        await compare_graphs([expected], [actual], mode)


@pytest.mark.parametrize('mode', ['freeze', 'recorded'])
@pytest.mark.parametrize('field', ['requested_revision', 'commit_id', 'vcs'])
async def test_only_requested_revision_equivalence_is_freeze_only(compare_graphs, mode, field):
    expected = {'name': 'amplifier-git', 'version': '1.0', 'directUrl': {
        'url': 'https://example.invalid/source',
        'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40, 'requested_revision': 'main'}}}
    actual = deepcopy(expected)
    actual['directUrl']['vcs_info'][field] = 'b' * 40
    if mode == 'freeze' and field == 'requested_revision':
        await compare_graphs([expected], [actual], mode)
    else:
        with pytest.raises(ValueError, match='differs from|changed after'):
            await compare_graphs([expected], [actual], mode)


@pytest.mark.parametrize('mode', ['freeze', 'recorded'])
@pytest.mark.parametrize('kind', ['missing-dir-info', 'non-file-url'])
async def test_editable_default_requires_a_local_directory(compare_graphs, mode, kind):
    expected, actual = local_record(), local_record()
    actual['directUrl']['dir_info']['editable'] = False
    if kind == 'missing-dir-info':
        del expected['directUrl']['dir_info']
    else:
        expected['directUrl']['url'] = actual['directUrl']['url'] = 'https://example.invalid/source'
    with pytest.raises(ValueError, match='differs from|changed after'):
        await compare_graphs([expected], [actual], mode)


@pytest.mark.parametrize('change', ['addition', 'replacement', 'removal'])
@pytest.mark.parametrize('allow_additions', [False, True])
async def test_recorded_additions_policy_keeps_qualified_packages(compare_graphs, change, allow_additions):
    expected, actual = local_record(), local_record()
    actual['directUrl']['dir_info']['editable'] = False
    if change == 'replacement':
        actual['version'] = '2.0'
    graph = [actual] if change != 'removal' else []
    graph.append({'name': 'new-module', 'version': '1.0'})
    if change == 'addition' and allow_additions:
        await compare_graphs([expected], graph, 'recorded', allow_additions=True)
    else:
        with pytest.raises(ValueError, match='changed after'):
            await compare_graphs([expected], graph, 'recorded', allow_additions=allow_additions)
