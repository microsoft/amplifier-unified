"""Disk-backed two-host acceptance for the execution ownership protocol."""

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import errno
import json
import os
from pathlib import Path
import stat
from threading import Barrier
import uuid

import pytest

from amplifier_portability.protocol import TransferNode
from amplifier_worktrees.git import atomic


SESSION = 'portable-task-fixture'
PAYLOAD = {
    'history': [{'role': 'user', 'content': 'Keep this fixture history.'}],
    'outputLineage': [{'id': 'output-one', 'parentId': None}],
    'configurationIntent': {'model': 'fixture-model'},
}
CHECKS = {
    'runtimeVerified': True,
    'accountVerified': True,
    'nativeFenceVerified': True,
    'credentialsOrigin': 'destination',
}
IMPORT_REQUEST = {'repository': 'provisioned-fixture-repository'}


@pytest.fixture
def hosts(tmp_path):
    source = TransferNode(tmp_path / 'source', 'Source fixture')
    destination = TransferNode(tmp_path / 'destination', 'Destination fixture')
    pair(source, destination)
    return source, destination


def pair(*nodes):
    # Trust files contain only public identities and are private on each host.
    for node in nodes:
        atomic(node.directory / 'peers.json', {
            peer.identity['id']: peer.identity
            for peer in nodes if peer is not node
        })


def reload(node):
    return TransferNode(node.directory, node.identity['label'])


def prepare(source, destination, command='export-one', sid=SESSION):
    row = source.begin(sid, destination.identity['id'], command, {'mode': 'clean'})
    row = source.prepared(row['id'], copy.deepcopy(PAYLOAD))
    return row, json.loads(Path(row['package']).read_text())


def reach(hosts, phase):
    source, destination = hosts
    outgoing = source.begin(SESSION, destination.identity['id'], 'export-one', {'mode': 'clean'})
    identity = outgoing['id']
    if phase == 'preparing':
        return identity
    outgoing = source.prepared(identity, copy.deepcopy(PAYLOAD))
    if phase == 'prepared':
        return identity
    envelope = json.loads(Path(outgoing['package']).read_text())
    incoming = destination.receive(envelope, IMPORT_REQUEST)
    if phase == 'staging':
        return identity
    incoming = destination.ready(identity, {'workingDirectory': 'fixture-checkout'}, CHECKS)
    if phase == 'ready':
        return identity
    outgoing = source.release(identity, incoming['readyReceipt'], outgoing['revision'])
    if phase == 'released':
        return identity
    destination.activating(identity, outgoing['releaseCertificate'], incoming['revision'])
    if phase == 'activating':
        return identity
    destination.active(identity)
    assert phase == 'active'
    return identity


def assert_unmodified(node, identity, operation):
    before = node.path(identity).read_bytes()
    with pytest.raises(ValueError):
        operation()
    assert node.path(identity).read_bytes() == before


def test_two_hosts_staged_transfer_has_exactly_one_execution_owner(hosts):
    source, destination = hosts
    assert source.identity['id'] != destination.identity['id']
    assert reload(source).identity == source.identity
    assert reload(destination).identity == destination.identity
    assert not source.fenced(SESSION)

    outgoing = source.begin(SESSION, destination.identity['id'], 'export-one', {'mode': 'clean'})
    identity = outgoing['id']
    assert outgoing['phase'] == 'preparing'
    assert reload(source).fenced(SESSION)
    outgoing = source.prepared(identity, copy.deepcopy(PAYLOAD))
    package = Path(outgoing['package'])
    envelope = json.loads(package.read_text())
    assert envelope['body']['payload'] == PAYLOAD
    assert outgoing['phase'] == 'prepared'
    assert source.fenced(SESSION)

    incoming = destination.receive(envelope, IMPORT_REQUEST)
    assert incoming['phase'] == 'staging'
    assert reload(destination).fenced(SESSION)
    incoming = destination.ready(identity, {'workingDirectory': 'fixture-checkout'}, CHECKS)
    assert incoming['phase'] == 'ready'
    assert destination.fenced(SESSION)
    outgoing = source.release(identity, incoming['readyReceipt'], outgoing['revision'])
    assert outgoing['phase'] == 'released'
    assert reload(source).fenced(SESSION)
    incoming = destination.activating(identity, outgoing['releaseCertificate'], incoming['revision'])
    assert incoming['phase'] == 'activating'
    assert reload(destination).fenced(SESSION)
    incoming = destination.active(identity)
    assert incoming['phase'] == 'active'
    assert not reload(destination).fenced(SESSION)
    assert reload(source).fenced(SESSION)
    assert json.loads(package.read_text()) == envelope
    assert all(row['inputsReplayed'] is False for node in hosts for row in node.records())
    for node in hosts:
        for path in [node.directory / 'identity.json', node.directory / 'peers.json', node.path(identity)]:
            assert path.stat().st_mode & 0o077 == 0
        assert all('privateKey' not in peer for peer in node.peers().values())
    assert package.stat().st_mode & 0o077 == 0


def test_begin_duplicate_preserves_prepared_record_and_package(hosts):
    source, destination = hosts
    outgoing, _ = prepare(source, destination)
    before = source.path(outgoing['id']).read_bytes()
    package_before = Path(outgoing['package']).read_bytes()
    duplicate = reload(source).begin(SESSION, destination.identity['id'], 'export-one', {'mode': 'clean'})
    assert duplicate == {**outgoing, 'duplicate': True}
    assert source.path(outgoing['id']).read_bytes() == before
    assert Path(outgoing['package']).read_bytes() == package_before
    assert len(source.records()) == 1
    assert_unmodified(source, outgoing['id'], lambda: source.prepared(outgoing['id'], PAYLOAD))


@pytest.mark.parametrize('same_command', [True, False])
def test_two_local_instances_cannot_start_two_transfers_for_one_task(hosts, same_command):
    source, destination = hosts
    instances = (reload(source), reload(source))
    barrier = Barrier(2)

    def begin(index):
        barrier.wait(timeout=5)
        try:
            return instances[index].begin(
                SESSION, destination.identity['id'],
                'contended-command' if same_command else f'contended-command-{index}', {},
            )
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(begin, range(2)))
    rows = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    if same_command:
        assert len(rows) == 2
        assert sum(row.get('duplicate', False) for row in rows) == 1
        assert rows[0]['id'] == rows[1]['id']
    else:
        assert len(rows) == 1
        assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
    assert len(reload(source).records(SESSION)) == 1
    assert source.fenced(SESSION)


@pytest.mark.parametrize('changed', ['session', 'destination', 'request'])
def test_begin_duplicate_rejects_different_request(hosts, tmp_path, changed):
    source, destination = hosts
    other = TransferNode(tmp_path / 'other', 'Other fixture')
    pair(source, destination, other)
    outgoing, _ = prepare(source, destination)
    assert_unmodified(source, outgoing['id'], lambda: source.begin(
        'different-session' if changed == 'session' else SESSION,
        other.identity['id'] if changed == 'destination' else destination.identity['id'],
        'export-one', {'mode': 'carry_dirty' if changed == 'request' else 'clean'},
    ))


@pytest.mark.parametrize('phase', ['staging', 'ready', 'active'])
def test_receive_duplicate_never_replays_destination_work(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, phase)
    envelope = json.loads(Path(source.get(identity)['package']).read_text())
    before = destination.path(identity).read_bytes()
    previous = destination.get(identity)
    result = reload(destination).receive(envelope, IMPORT_REQUEST)
    assert result == {**previous, 'duplicate': True}
    assert destination.path(identity).read_bytes() == before
    assert len(destination.records()) == 1
    assert_unmodified(destination, identity, lambda: destination.receive(envelope, {'repository': 'different'}))
    altered = copy.deepcopy(envelope['body'])
    altered['payload']['history'].append({'role': 'user', 'content': 'Different history.'})
    assert_unmodified(destination, identity, lambda: destination.receive(source.sign(altered), IMPORT_REQUEST))


def test_release_duplicate_retains_certificate_and_rejects_another_readiness(hosts):
    source, destination = hosts
    identity = reach(hosts, 'released')
    outgoing = source.get(identity)
    ready = destination.get(identity)['readyReceipt']
    before = source.path(identity).read_bytes()
    assert reload(source).release(identity, ready, 2) == {**outgoing, 'duplicate': True}
    assert source.path(identity).read_bytes() == before
    changed = copy.deepcopy(ready['body'])
    changed['checksHash'] = '0' * 64
    assert_unmodified(source, identity, lambda: source.release(identity, destination.sign(changed), 2))


def test_activation_duplicate_does_not_repeat_effects(hosts):
    source, destination = hosts
    identity = reach(hosts, 'active')
    certificate = source.get(identity)['releaseCertificate']
    incoming = destination.get(identity)
    before = destination.path(identity).read_bytes()
    assert reload(destination).activating(identity, certificate, 2) == {**incoming, 'duplicate': True}
    assert destination.path(identity).read_bytes() == before
    assert_unmodified(destination, identity, lambda: destination.active(identity))


@pytest.mark.parametrize('key,value', [
    ('runtimeVerified', False), ('accountVerified', False), ('nativeFenceVerified', False),
    ('runtimeVerified', 1), ('accountVerified', None), ('credentialsOrigin', 'source'),
])
def test_ready_requires_locally_verified_destination_prerequisites(hosts, key, value):
    _, destination = hosts
    identity = reach(hosts, 'staging')
    checks = {**CHECKS, key: value}
    assert_unmodified(destination, identity, lambda: destination.ready(identity, {}, checks))
    assert destination.fenced(SESSION)


def test_stale_revisions_cannot_release_or_activate(hosts):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    incoming = destination.get(identity)
    assert_unmodified(source, identity, lambda: source.release(identity, incoming['readyReceipt'], 1))
    released = source.release(identity, incoming['readyReceipt'], source.get(identity)['revision'])
    assert_unmodified(destination, identity, lambda: destination.activating(identity, released['releaseCertificate'], 1))
    assert source.fenced(SESSION) and destination.fenced(SESSION)


@pytest.mark.parametrize('kind', ['capsule', 'ready', 'release'])
@pytest.mark.parametrize('tamper', ['body', 'signature', 'signer'])
def test_tampered_signed_receipts_are_rejected_without_journal_mutation(hosts, kind, tamper):
    source, destination = hosts
    identity = reach(hosts, 'released' if kind == 'release' else 'ready' if kind == 'ready' else 'prepared')
    if kind == 'capsule':
        envelope = json.loads(Path(source.get(identity)['package']).read_text())
        receiver = destination
        invoke = lambda value: destination.receive(value, IMPORT_REQUEST)
    elif kind == 'ready':
        envelope = destination.get(identity)['readyReceipt']
        receiver = source
        invoke = lambda value: source.release(identity, value, source.get(identity)['revision'])
    else:
        envelope = source.get(identity)['releaseCertificate']
        receiver = destination
        invoke = lambda value: destination.activating(identity, value, destination.get(identity)['revision'])
    envelope = copy.deepcopy(envelope)
    if tamper == 'body':
        envelope['body']['sessionId'] = 'different-session'
    elif tamper == 'signature':
        raw = bytearray(base64.b64decode(envelope['signature']))
        raw[0] ^= 1
        envelope['signature'] = base64.b64encode(raw).decode()
    else:
        envelope['signer'] = receiver.identity['id']
    before = receiver.records()
    with pytest.raises(ValueError):
        invoke(envelope)
    assert receiver.records() == before


@pytest.mark.parametrize('kind', ['ready', 'release'])
@pytest.mark.parametrize('field', ['id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash'])
def test_valid_signatures_cannot_substitute_a_different_transfer(hosts, kind, field):
    source, destination = hosts
    identity = reach(hosts, 'ready' if kind == 'ready' else 'released')
    row = destination.get(identity) if kind == 'ready' else source.get(identity)
    body = copy.deepcopy(row['readyReceipt' if kind == 'ready' else 'releaseCertificate']['body'])
    body[field] = body[field] + 1 if field == 'generation' else str(uuid.uuid4())
    if kind == 'ready':
        assert_unmodified(source, identity, lambda: source.release(identity, destination.sign(body), 2))
    else:
        assert_unmodified(destination, identity, lambda: destination.activating(identity, source.sign(body), 2))


def test_release_must_acknowledge_the_exact_destination_readiness(hosts):
    source, destination = hosts
    identity = reach(hosts, 'released')
    body = copy.deepcopy(source.get(identity)['releaseCertificate']['body'])
    body['readyHash'] = '0' * 64
    assert_unmodified(destination, identity, lambda: destination.activating(identity, source.sign(body), 2))


@pytest.mark.parametrize('field', ['source', 'destination'])
def test_capsule_must_belong_to_the_signer_and_receiving_host(hosts, field):
    source, destination = hosts
    _, envelope = prepare(source, destination)
    envelope['body'][field] = '0' * 64
    with pytest.raises(ValueError, match='different execution hosts'):
        destination.receive(source.sign(envelope['body']), IMPORT_REQUEST)
    assert not destination.records()


@pytest.mark.parametrize('phase', ['preparing', 'staging', 'activating'])
def test_restart_marks_unconfirmed_effects_unknown_without_replay(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, phase)
    original = source if phase == 'preparing' else destination
    before = original.get(identity)
    restarted = reload(original)
    restarted.recover()
    row = restarted.get(identity)
    assert row['phase'] == 'unknown'
    assert row['previousPhase'] == phase
    assert row['revision'] == before['revision'] + 1
    assert row['inputsReplayed'] is False
    assert restarted.fenced(SESSION)
    persisted = restarted.path(identity).read_bytes()
    reload(restarted).recover()
    assert restarted.path(identity).read_bytes() == persisted
    assert_unmodified(restarted, identity, lambda: restarted.begin(
        SESSION, (destination if phase == 'preparing' else source).identity['id'], 'retry-as-new', {},
    ))


@pytest.mark.parametrize('phase', ['prepared', 'ready', 'released', 'active'])
def test_restart_preserves_confirmed_boundaries(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, phase)
    target = source if phase in {'prepared', 'released'} else destination
    before = target.path(identity).read_bytes()
    reload(target).recover()
    assert target.path(identity).read_bytes() == before
    assert target.fenced(SESSION) is (phase != 'active')


@pytest.mark.parametrize('phase', ['released', 'activating', 'active'])
def test_lost_activation_acknowledgement_never_allows_source_reclaim(hosts, phase):
    source, _ = hosts
    identity = reach(hosts, phase)
    restarted = reload(source)
    before = restarted.path(identity).read_bytes()
    assert restarted.unknown(identity)['phase'] == 'released'
    restarted.recover()
    assert restarted.path(identity).read_bytes() == before
    assert restarted.fenced(SESSION)
    assert_unmodified(restarted, identity, lambda: restarted.begin(
        SESSION, hosts[1].identity['id'], 'reclaim-after-lost-ack', {},
    ))


def test_round_trip_generation_supersedes_only_the_older_source_tombstone(hosts):
    source, destination = hosts
    first = reach(hosts, 'active')
    outgoing, envelope = prepare(destination, source, command='return-to-original-host')
    assert outgoing['generation'] == 2
    assert source.fenced(SESSION) and destination.fenced(SESSION)
    identity = outgoing['id']
    source.receive(envelope, IMPORT_REQUEST)
    incoming = source.ready(identity, {'workingDirectory': 'returned-fixture-checkout'}, CHECKS)
    assert source.fenced(SESSION)
    released = destination.release(identity, incoming['readyReceipt'], outgoing['revision'])
    source.activating(identity, released['releaseCertificate'], incoming['revision'])
    assert source.fenced(SESSION)
    source.active(identity)
    assert not reload(source).fenced(SESSION)
    assert reload(destination).fenced(SESSION)
    assert source.get(first)['phase'] == 'released'
    assert source.get(identity)['phase'] == 'active'
    assert len(source.records(SESSION)) == len(destination.records(SESSION)) == 2
    with pytest.raises(ValueError):
        destination.begin(SESSION, source.identity['id'], 'reclaim-on-return', {})


@pytest.mark.parametrize('phase', ['preparing', 'prepared', 'unknown'])
def test_returning_task_cannot_arrive_before_previous_source_release(hosts, phase):
    source, destination = hosts
    first = reach(hosts, 'preparing' if phase == 'preparing' else 'prepared')
    if phase == 'unknown':
        source.unknown(first)
    body = {
        'id': str(uuid.uuid4()), 'sessionId': SESSION, 'generation': 2,
        'source': destination.identity['id'], 'destination': source.identity['id'],
        'kind': 'capsule', 'version': 1, 'payload': copy.deepcopy(PAYLOAD),
    }
    before = source.records()
    with pytest.raises(ValueError, match='already owns|unresolved'):
        source.receive(destination.sign(body), IMPORT_REQUEST)
    assert source.records() == before


def test_returning_task_rejects_stale_generation_after_source_release(hosts):
    source, destination = hosts
    identity = reach(hosts, 'released')
    original = json.loads(Path(source.get(identity)['package']).read_text())
    body = {
        **original['body'], 'id': str(uuid.uuid4()),
        'source': destination.identity['id'], 'destination': source.identity['id'],
    }
    before = source.records()
    with pytest.raises(ValueError, match='already owns|unresolved'):
        source.receive(destination.sign(body), IMPORT_REQUEST)
    assert source.records() == before


def test_returning_task_must_come_from_the_previous_destination(hosts, tmp_path):
    source, destination = hosts
    other = TransferNode(tmp_path / 'other', 'Other paired fixture')
    pair(source, destination, other)
    identity = reach(hosts, 'released')
    original = json.loads(Path(source.get(identity)['package']).read_text())
    body = {
        **original['body'], 'id': str(uuid.uuid4()), 'generation': 2,
        'source': other.identity['id'], 'destination': source.identity['id'],
    }
    before = source.records()
    with pytest.raises(ValueError, match='already owns|unresolved'):
        source.receive(other.sign(body), IMPORT_REQUEST)
    assert source.records() == before


@pytest.mark.parametrize('generation', [0, -1, True, 1.0, '1', None])
def test_incoming_generation_requires_a_positive_integer(hosts, generation):
    source, destination = hosts
    _, envelope = prepare(source, destination)
    body = copy.deepcopy(envelope['body'])
    body['generation'] = generation
    with pytest.raises(ValueError):
        destination.receive(source.sign(body), IMPORT_REQUEST)
    assert not destination.records()


def test_unpaired_signer_cannot_stage_a_task(hosts, tmp_path):
    _, destination = hosts
    stranger = TransferNode(tmp_path / 'stranger', 'Unpaired fixture')
    body = {
        'id': str(uuid.uuid4()), 'sessionId': SESSION, 'generation': 1,
        'source': stranger.identity['id'], 'destination': destination.identity['id'],
        'kind': 'capsule', 'version': 1, 'payload': copy.deepcopy(PAYLOAD),
    }
    with pytest.raises(ValueError, match='paired host'):
        destination.receive(stranger.sign(body), IMPORT_REQUEST)
    assert not destination.records()


@pytest.mark.parametrize('bad_trust', ['public_permissions', 'symlink', 'wrong_fingerprint'])
def test_peer_trust_file_cannot_be_public_aliased_or_fingerprint_mismatched(hosts, tmp_path, bad_trust):
    source, destination = hosts
    _, envelope = prepare(source, destination)
    path = destination.directory / 'peers.json'
    if bad_trust == 'public_permissions':
        path.chmod(0o644)
    elif bad_trust == 'symlink':
        saved = tmp_path / 'aliased-peers.json'
        path.rename(saved)
        path.symlink_to(saved)
    else:
        atomic(path, {source.identity['id']: destination.identity})
    with pytest.raises(ValueError):
        destination.receive(envelope, IMPORT_REQUEST)
    assert not destination.records()


@pytest.mark.parametrize('body', [None, [], 'invalid'])
def test_signed_malformed_receipt_body_is_a_controlled_rejection(hosts, body):
    source, destination = hosts
    with pytest.raises(ValueError):
        destination.receive(source.sign(body), IMPORT_REQUEST)
    assert not destination.records()


def test_source_cancel_and_authenticated_discard_allow_only_a_fresh_generation(hosts):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    outgoing, incoming = source.get(identity), destination.get(identity)
    original_package = Path(outgoing['package']).read_bytes()
    cancelled = source.cancel(identity, outgoing['revision'], 'Keep this task on the original host.')
    assert cancelled['phase'] == 'cancelled'
    assert not reload(source).fenced(SESSION)
    assert reload(destination).fenced(SESSION)
    discarded = destination.discard(identity, cancelled['cancelCertificate'], incoming['revision'])
    assert discarded['phase'] == 'discarded'
    assert reload(destination).fenced(SESSION)
    assert Path(outgoing['package']).read_bytes() == original_package
    assert_unmodified(source, identity, lambda: source.release(identity, incoming['readyReceipt'], cancelled['revision']))
    assert_unmodified(source, identity, lambda: source.releasing(identity, incoming['readyReceipt'], cancelled['revision'], source.directory / 'archive'))
    assert_unmodified(destination, identity, lambda: destination.begin(SESSION, source.identity['id'], 'claim-cancelled-task', {}))
    assert source.begin(SESSION, destination.identity['id'], 'export-one', {'mode': 'clean'})['phase'] == 'cancelled'
    assert destination.receive(json.loads(original_package), IMPORT_REQUEST)['phase'] == 'discarded'

    next_outgoing, envelope = prepare(source, destination, command='export-after-cancellation')
    assert next_outgoing['generation'] == 2
    next_identity = next_outgoing['id']
    staged = destination.receive(envelope, IMPORT_REQUEST)
    ready = destination.ready(next_identity, {}, CHECKS)
    released = source.release(next_identity, ready['readyReceipt'], next_outgoing['revision'])
    destination.activating(next_identity, released['releaseCertificate'], ready['revision'])
    destination.active(next_identity)
    assert staged['generation'] == 2
    assert source.fenced(SESSION) and not destination.fenced(SESSION)
    assert source.get(identity)['phase'] == 'cancelled'
    assert destination.get(identity)['phase'] == 'discarded'


def test_cancel_and_discard_retries_preserve_terminal_receipts_after_restart(hosts):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Reviewed cancellation.')
    discarded = destination.discard(identity, cancelled['cancelCertificate'], destination.get(identity)['revision'])
    for original, result in ((source, cancelled), (destination, discarded)):
        before = original.path(identity).read_bytes()
        restarted = reload(original)
        restarted.recover()
        restarted.unknown(identity)
        assert restarted.path(identity).read_bytes() == before
        if original is source:
            retry = restarted.cancel(identity, 2, 'Reviewed cancellation.')
        else:
            retry = restarted.discard(identity, cancelled['cancelCertificate'], 2)
        assert retry == {**result, 'duplicate': True}
        assert restarted.path(identity).read_bytes() == before


def test_interrupted_preparation_can_be_cancelled_without_creating_a_capsule(hosts):
    source, destination = hosts
    identity = reach(hosts, 'preparing')
    restarted = reload(source)
    restarted.recover()
    unknown = restarted.get(identity)
    assert unknown['previousPhase'] == 'preparing'
    cancelled = restarted.cancel(identity, unknown['revision'], 'Inspected interrupted export; no release exists.')
    verified = destination.verify(cancelled['cancelCertificate'], kind='cancel', signer=source.identity['id'])
    assert verified['capsuleHash'] is None
    assert cancelled['phase'] == 'cancelled' and not restarted.fenced(SESSION)
    assert not list((source.directory / 'packages').glob('*.json'))
    assert restarted.begin(SESSION, destination.identity['id'], 'new-export', {})['generation'] == 2


@pytest.mark.parametrize('reason', ['', '   ', None, 7])
def test_cancellation_requires_a_review_reason(hosts, reason):
    source, destination = hosts
    row, _ = prepare(source, destination)
    assert_unmodified(source, row['id'], lambda: source.cancel(row['id'], row['revision'], reason))
    assert source.fenced(SESSION)


def test_cancellation_rejects_a_stale_source_revision(hosts):
    source, destination = hosts
    row, _ = prepare(source, destination)
    assert_unmodified(source, row['id'], lambda: source.cancel(row['id'], 1, 'Reviewed cancellation.'))


@pytest.mark.parametrize('phase', ['preparing', 'releasing', 'unknown-release', 'released'])
def test_source_cannot_cancel_an_unconfirmed_or_started_release(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, 'preparing' if phase == 'preparing' else 'ready')
    if phase in {'releasing', 'unknown-release'}:
        source.releasing(identity, destination.get(identity)['readyReceipt'], source.get(identity)['revision'], source.directory / 'archive')
        if phase == 'unknown-release':
            source = reload(source)
            source.recover()
    elif phase == 'released':
        source.release(identity, destination.get(identity)['readyReceipt'], source.get(identity)['revision'])
    assert_unmodified(source, identity, lambda: source.cancel(identity, source.get(identity)['revision'], 'Attempted source reclamation.'))
    assert source.fenced(SESSION)


@pytest.mark.parametrize('phase', ['ready', 'unknown', 'active'])
def test_destination_cannot_cancel_source_ownership(hosts, phase):
    _, destination = hosts
    identity = reach(hosts, 'active' if phase == 'active' else 'ready')
    if phase == 'unknown':
        destination.unknown(identity)
    assert_unmodified(destination, identity, lambda: destination.cancel(identity, destination.get(identity)['revision'], 'Not the source.'))


@pytest.mark.parametrize('release_boundary', ['release', 'releasing'])
def test_simultaneous_cancel_and_release_have_one_durable_winner(hosts, release_boundary):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    revision = source.get(identity)['revision']
    ready = destination.get(identity)['readyReceipt']
    instances = reload(source), reload(source)
    barrier = Barrier(2)

    def compete(index):
        barrier.wait(timeout=5)
        try:
            if index == 0:
                return instances[index].cancel(identity, revision, 'Concurrent reviewed cancellation.')
            if release_boundary == 'releasing':
                return instances[index].releasing(identity, ready, revision, source.directory / 'archive')
            return instances[index].release(identity, ready, revision)
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, range(2)))
    successful = [row for row in results if isinstance(row, dict)]
    assert len(successful) == 1 and sum(isinstance(row, ValueError) for row in results) == 1
    persisted = reload(source).get(identity)
    assert persisted == successful[0]
    assert not ('cancelCertificate' in persisted and 'releaseCertificate' in persisted)
    assert persisted['phase'] in {'cancelled', 'released' if release_boundary == 'release' else 'releasing'}
    assert source.fenced(SESSION) is (persisted['phase'] != 'cancelled')


@pytest.mark.parametrize('phase', ['ready', 'unknown-staging', 'unknown-ready'])
def test_authenticated_source_cancellation_can_discard_unactivated_destination(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, 'staging' if phase == 'unknown-staging' else 'ready')
    if phase == 'unknown-staging':
        destination = reload(destination)
        destination.recover()
    elif phase == 'unknown-ready':
        destination.unknown(identity)
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Source remains owner.')
    result = destination.discard(identity, cancelled['cancelCertificate'], destination.get(identity)['revision'])
    assert result['phase'] == 'discarded' and destination.fenced(SESSION)
    assert result['inputsReplayed'] is False


@pytest.mark.parametrize('field', ['id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash'])
def test_authenticated_discard_rejects_cancellation_for_another_transfer(hosts, field):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Reviewed cancellation.')
    body = copy.deepcopy(cancelled['cancelCertificate']['body'])
    body[field] = body[field] + 1 if field == 'generation' else str(uuid.uuid4())
    assert_unmodified(destination, identity, lambda: destination.discard(identity, source.sign(body), destination.get(identity)['revision']))


@pytest.mark.parametrize('tamper', ['signature', 'signer', 'kind'])
def test_discard_authenticates_the_original_source_cancel_certificate(hosts, tamper, tmp_path):
    source, destination = hosts
    other = TransferNode(tmp_path / 'other', 'Other paired host')
    pair(source, destination, other)
    identity = reach(hosts, 'ready')
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Reviewed cancellation.')
    certificate = copy.deepcopy(cancelled['cancelCertificate'])
    if tamper == 'signature':
        certificate['signature'] = base64.b64encode(b'\0' * 64).decode()
    elif tamper == 'signer':
        certificate = other.sign(certificate['body'])
    else:
        certificate['body']['kind'] = 'release'
        certificate = source.sign(certificate['body'])
    assert_unmodified(destination, identity, lambda: destination.discard(identity, certificate, destination.get(identity)['revision']))


@pytest.mark.parametrize('phase', ['activating', 'unknown-activating', 'active'])
def test_discard_never_revokes_a_destination_that_holds_source_release(hosts, phase):
    source, destination = hosts
    identity = reach(hosts, 'active' if phase == 'active' else 'activating')
    if phase == 'unknown-activating':
        destination = reload(destination)
        destination.recover()
    body = {key: destination.get(identity)[key] for key in ('id', 'sessionId', 'generation', 'source', 'destination', 'capsuleHash')}
    body.update(kind='cancel', version=1)
    assert_unmodified(destination, identity, lambda: destination.discard(identity, source.sign(body), destination.get(identity)['revision']))


def test_discard_requires_the_current_destination_revision(hosts):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Reviewed cancellation.')
    assert_unmodified(destination, identity, lambda: destination.discard(identity, cancelled['cancelCertificate'], 1))


@pytest.mark.parametrize('changed', ['same-generation', 'other-source'])
def test_discarded_destination_accepts_only_a_later_generation_from_original_source(hosts, changed, tmp_path):
    source, destination = hosts
    other = TransferNode(tmp_path / 'other', 'Other paired host')
    pair(source, destination, other)
    identity = reach(hosts, 'ready')
    cancelled = source.cancel(identity, source.get(identity)['revision'], 'Reviewed cancellation.')
    destination.discard(identity, cancelled['cancelCertificate'], destination.get(identity)['revision'])
    outgoing, envelope = prepare(source, destination, command='export-after-cancel')
    body = copy.deepcopy(envelope['body'])
    signer = source
    if changed == 'same-generation':
        body['generation'] = outgoing['generation'] - 1
    else:
        signer = other
        body['source'] = other.identity['id']
    before = destination.records()
    with pytest.raises(ValueError):
        destination.receive(signer.sign(body), IMPORT_REQUEST)
    assert destination.records() == before


def test_releasing_restart_remains_fenced_and_requires_the_original_ready_receipt(hosts):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    ready = destination.get(identity)['readyReceipt']
    source.releasing(identity, ready, source.get(identity)['revision'], source.directory / 'archive')
    restarted = reload(source)
    restarted.recover()
    unknown = restarted.get(identity)
    assert unknown['phase'] == 'unknown' and unknown['previousPhase'] == 'releasing'
    assert restarted.fenced(SESSION)
    changed = copy.deepcopy(ready['body'])
    changed['checksHash'] = '0' * 64
    assert_unmodified(restarted, identity, lambda: restarted.releasing(identity, destination.sign(changed), unknown['revision'], source.directory / 'archive'))
    recovered = restarted.releasing(identity, ready, unknown['revision'], source.directory / 'archive')
    released = restarted.release(identity, ready, recovered['revision'])
    assert released['phase'] == 'released' and restarted.fenced(SESSION)


def test_directory_io_failure_never_acknowledges_release_on_first_attempt_or_retry(hosts, monkeypatch):
    source, destination = hosts
    identity = reach(hosts, 'ready')
    ready = destination.get(identity)['readyReceipt']
    revision = source.get(identity)['revision']
    fsync = os.fsync
    failures = []

    def fail_after_release_replace(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            visible = json.loads(source.path(identity).read_text())
            if visible['phase'] == 'released':
                failures.append(True)
                raise OSError(errno.EIO, 'Injected directory durability failure')
        return fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail_after_release_replace)
        with pytest.raises(OSError) as initial:
            source.release(identity, ready, revision)
        assert initial.value.errno == errno.EIO
        visible = source.path(identity).read_bytes()
        assert json.loads(visible)['phase'] == 'released'
        assert source.fenced(SESSION)
        with pytest.raises(OSError) as retry:
            source.release(identity, ready, revision)
        assert retry.value.errno == errno.EIO
        assert source.path(identity).read_bytes() == visible
        assert len(failures) == 2
    duplicate = reload(source).release(identity, ready, revision)
    assert duplicate['duplicate'] and duplicate['phase'] == 'released'
    assert source.path(identity).read_bytes() == visible


def test_existing_directory_retry_cannot_bypass_failed_ancestor_sync(tmp_path, monkeypatch):
    directory = tmp_path / 'new-parent' / 'host'
    fsync = os.fsync

    def fail_directories(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, 'Injected ancestor durability failure')
        return fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail_directories)
        for _ in range(2):
            with pytest.raises(OSError) as failed:
                TransferNode(directory, 'Unconfirmed host')
            assert failed.value.errno == errno.EIO
            assert not (directory / 'identity.json').exists()
    first = TransferNode(directory, 'Confirmed host')
    assert reload(first).identity == first.identity
