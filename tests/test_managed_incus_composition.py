"""Actual core stage/qualification receipts followed by engine-only rebinding."""
import copy
from pathlib import Path

import pytest

from test_managed_runner_updates import app, source, qualified, RUNTIME
from test_managed_incus_target import engine, qualify
from amplifier_web import managed_runner_updates as core
from amplifier_web import managed_runner_qualification as core_probe
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_setup as private
from amplifier_web import incus_engine_core_binding as composition
from amplifier_web import incus_engine_generations as generations
from amplifier_web.managed_runner_binding import execution_binding


async def selected_core(app, engine, monkeypatch):
    raw = core.raw_configuration(app)
    raw.pop('dockerHost'); raw.update(backend='incus', incus=engine.previous)
    private.write_json(app.data_dir / 'test-environments.json', raw)
    path = private.root(app) / ('c' * 32 + '.json')
    receipt = private.read_json(path); receipt['candidateConfiguration'] = raw
    private.write_json(path, receipt)
    app.smart_tools.state['installations'][1]['runnerCandidate'] = True
    candidate = await core.stage(app, source(app))
    monkeypatch.setattr(core_probe, 'advance', qualified)
    assert (await core.advance(app, candidate['runnerUpdate']))['complete']
    current = core.raw_configuration(app)
    assert current['runnerQualificationRequired'] is True
    return current


async def test_core_engine_composition_preserves_runtime_binding_rollback_and_old_owners(app, engine, monkeypatch):
    current = await selected_core(app, engine, monkeypatch)
    previous_id = core.rollback_target(app, current)
    candidate = {**current, 'incus': qualify(engine)['incus']}
    monkeypatch.setattr(composition, 'runtime_identity', lambda python: copy.deepcopy(RUNTIME))
    composition.compose(app.data_dir / 'test-environments.json', current, candidate, engine.path)
    private.write_json(app.data_dir / 'test-environments.json', candidate)
    normalized, status, _ = environments.configuration(app)
    assert status == 'ready'
    core.validate_execution(app.data_dir, normalized)
    proof = private.read_json(core.qualification_path(app.data_dir, normalized))
    assert proof['proof']['kind'] == 'composed-core-engine'
    assert set(proof['composition']) == {'core', 'engine'}
    assert core.trusted_installation(app, current) and core.trusted_installation(app, candidate)
    assert core.rollback_target(app, candidate) == previous_id
    assert core.referenced(app.data_dir, previous_id)
    assert core.rollback(app, candidate['installationId'])
    assert core.raw_configuration(app)['installationId'] == previous_id


@pytest.mark.parametrize('damage', ['missing-core', 'bad-core-proof', 'core-bytes', 'other-setting', 'other-engine-owner'])
async def test_missing_or_changed_evidence_never_creates_composed_binding(app, engine, monkeypatch, damage):
    current = await selected_core(app, engine, monkeypatch)
    candidate = {**current, 'incus': qualify(engine)['incus']}
    monkeypatch.setattr(composition, 'runtime_identity', lambda python: copy.deepcopy(RUNTIME))
    normalized, _, _ = environments.configuration(app)
    original = core.qualification_path(app.data_dir, normalized)
    if damage == 'missing-core':
        original.unlink()
    elif damage == 'bad-core-proof':
        proof = private.read_json(original); proof['proof']['checkPassed'] = False
        private.write_json(original, proof)
    elif damage == 'core-bytes':
        monkeypatch.setattr(composition, 'runtime_identity', lambda python: {'runtimeSha256': 'changed'})
    elif damage == 'other-setting':
        candidate['capacityPolicyId'] = 'c' * 64
    path = engine.path if damage != 'other-engine-owner' else engine.path.parent / ('e' * 32)
    with pytest.raises((ValueError, FileNotFoundError)):
        composition.compose(app.data_dir / 'test-environments.json', current, candidate, path)
    assert core.raw_configuration(app) == current


async def test_mutable_limits_keep_core_and_engine_policy_and_valid_binding(app, engine, monkeypatch):
    current = await selected_core(app, engine, monkeypatch)
    candidate = {**current, 'incus': qualify(engine)['incus']}
    monkeypatch.setattr(composition, 'runtime_identity', lambda python: copy.deepcopy(RUNTIME))
    current = copy.deepcopy(current); current['incus']['maxRunning'] = 3
    candidate['incus']['maxRunning'] = 3
    composition.compose(app.data_dir / 'test-environments.json', current, candidate, engine.path)
    private.write_json(app.data_dir / 'test-environments.json', candidate)
    assert core.setup_policy(app, candidate)['ref'] == 'main'
    edited = copy.deepcopy(candidate); edited['incus']['maxRunning'] = 6
    assert core.setup_policy(app, edited)['ref'] == 'main'
    assert core.rollback_target(app, edited)
    assert core.trusted_installation(app, edited)


async def test_core_only_capacity_edits_keep_setup_policy_and_qualified_rollback(app, monkeypatch):
    current = core.raw_configuration(app)
    current['maxRunning'] = 8
    private.write_json(app.data_dir / 'test-environments.json', current)
    assert core.setup_policy(app, current)['ref'] == 'main'
    app.smart_tools.state['installations'][1]['runnerCandidate'] = True
    candidate = await core.stage(app, source(app))
    monkeypatch.setattr(core_probe, 'advance', qualified)
    assert (await core.advance(app, candidate['runnerUpdate']))['complete']
    selected = core.raw_configuration(app)
    selected['maxInstances'] = 12
    private.write_json(app.data_dir / 'test-environments.json', selected)
    assert core.setup_policy(app, selected)['ref'] == 'main'
    assert core.rollback_target(app, selected) == current['installationId']
    assert core.trusted_installation(app, selected)
