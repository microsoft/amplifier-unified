"""Post-publication checks bind actual release bytes and report missing proof."""
import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('post_release_checks', ROOT / 'scripts/post_release_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def test_published_identity_uses_candidate_not_later_workflow_head():
    candidate = {'version': '1.2.3', 'revision': 'a' * 40}
    release = {'tagName': 'v1.2.3', 'isDraft': False, 'isPrerelease': False, 'publishedAt': 'now'}
    assert checks.resolve_candidate(candidate, release, 'a' * 40)['revision'] == 'a' * 40
    with pytest.raises(ValueError, match='differs'):
        checks.resolve_candidate(candidate, release, 'b' * 40)
    for key, value in [('isDraft', True), ('isPrerelease', True), ('publishedAt', None), ('tagName', 'v1.2.4')]:
        with pytest.raises(ValueError, match='published stable'):
            checks.resolve_candidate(candidate, {**release, key: value}, 'a' * 40)


@pytest.mark.parametrize('version', ['main', '../1.2.3', '1.2.3\nrevision=main', '1.2.3-rc1', None])
def test_invalid_release_identifiers_are_rejected(version):
    with pytest.raises(ValueError):
        checks.stable_version(version)


@pytest.mark.parametrize('mutation', ['none', 'bytes', 'missing', 'duplicate', 'path'])
def test_both_published_distributions_must_match_checksums(tmp_path, mutation):
    names = ['amplifier_unified-1.2.3-py3-none-any.whl', 'amplifier_unified-1.2.3.tar.gz']
    lines = []
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
        lines.append(hashlib.sha256(name.encode()).hexdigest() + '  ' + name)
    if mutation == 'bytes':
        (tmp_path / names[0]).write_bytes(b'changed')
    elif mutation == 'missing':
        lines.pop()
    elif mutation == 'duplicate':
        lines.append(lines[0])
    elif mutation == 'path':
        lines[0] = lines[0].replace(names[0], '../' + names[0])
    (tmp_path / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
    if mutation == 'none':
        assert checks.verify_downloads(tmp_path, '1.2.3')['verified']
    else:
        with pytest.raises(ValueError):
            checks.verify_downloads(tmp_path, '1.2.3')
        assert not (tmp_path / 'verification.json').exists()


def results():
    return {name: {'result': 'success'} for name in checks.CHECKS}


@pytest.mark.parametrize('outcome', ['failure', 'cancelled', 'skipped', None])
def test_missing_or_failed_checks_are_not_reported_as_success(outcome):
    value = results()
    if outcome is None:
        value.pop('browser')
    else:
        value['browser']['result'] = outcome
    report = checks.build_report(value, 'owner/app', '12', '11')
    assert report['failed'] == ['browser']
    assert 'publication-run-11' in report['title']
    assert 'actions/runs/12' in report['body']


@pytest.mark.parametrize('failed,existing', [(False, False), (True, False), (True, True), (False, True)])
def test_failure_tracking_is_per_release_and_recovery_closes_it(monkeypatch, failed, existing):
    value = results()
    value['resolve']['outputs'] = {'tag': 'v1.2.3', 'revision': 'a' * 40}
    if failed:
        value['readiness']['result'] = 'failure'
    report = checks.build_report(value, 'owner/app', '12', '11')
    calls = []
    def gh(*args, payload=None):
        calls.append((args, payload))
        if '--paginate' in args:
            return [[{'number': 10, 'body': report['marker']}] if existing else []]
        return {}
    monkeypatch.setattr(checks, 'gh', gh)
    checks.track_report('owner/app', report)
    writes = [(args, payload) for args, payload in calls if payload is not None]
    if not failed and not existing:
        assert writes == []
    elif existing:
        assert len(writes) == 1 and 'PATCH' in writes[0][0]
        assert writes[0][1]['state'] == ('open' if failed else 'closed')
    else:
        assert len(writes) == 1 and 'POST' in writes[0][0]
        assert writes[0][1]['title'] == 'Post-release verification: v1.2.3'


def test_fast_required_check_runs_on_every_pr_without_exhaustive_suites():
    workflow = yaml.load((ROOT / '.github/workflows/work-workflows.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['pull_request'] == ''
    job = workflow['jobs']['integration']
    assert job['name'] == 'Fast release checks'
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'test_post_release_checks.py' in commands
    assert 'npm test' in commands and 'git diff --exit-code' in commands
    assert 'playwright' not in commands and 'node tests/' not in commands
