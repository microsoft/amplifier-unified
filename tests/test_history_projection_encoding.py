"""Byte/budget/lifecycle proof for the bounded spool encoder, without a model."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import threading

import pytest

from amplifier_web import history_projection_process as transport


def expected_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def observe_writes(monkeypatch, callback):
    original = transport.os.fdopen

    class Observed:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            callback(self.stream, data)
            return self.stream.write(data)

    monkeypatch.setattr(transport.os, 'fdopen', lambda *a, **kw: Observed(original(*a, **kw)))


@pytest.mark.parametrize('value', [
    {}, {'z': [], 'a': {'é': '雪😀\\\n"', 'null': None, 'bool': True}},
    {'float': [-0.0, 1.25, 1e-30], 'integer': 2 ** 70},
    {'rows': [{'id': index, 'name': 'é雪😀'} for index in range(5000)]},
    {'value': '雪😀' * 30000},
])
def test_exact_bytes_digest_private_mode_and_bounded_writes(tmp_path, monkeypatch, value):
    owner = transport.ProcessProjector(tmp_path)
    expected = expected_bytes(value)
    writes = []
    observe_writes(monkeypatch, lambda stream, data: writes.append(len(data)))
    path = tmp_path / 'exact.input'
    size, digest = owner._encode(path, value)
    assert path.read_bytes() == expected
    assert size == owner._input_bytes == len(expected)
    assert digest == hashlib.sha256(expected).hexdigest()
    assert 0 < max(writes) <= transport._ENCODE_BATCH_BYTES
    if os.name == 'posix':
        assert path.stat().st_mode & 0o777 == 0o600
    owner._release(size)
    assert owner._input_bytes == 0


def test_many_tiny_fragments_share_bounded_writes(tmp_path, monkeypatch):
    value = {'rows': [{'id': index, 'name': 'small'} for index in range(5272)]}
    owner = transport.ProcessProjector(tmp_path)
    writes = []
    observe_writes(monkeypatch, lambda stream, data: writes.append(len(data)))
    size, _ = owner._encode(tmp_path / 'batch.input', value)
    # A structural witness, not a timing threshold or a prescribed exact count.
    assert len(writes) < 10
    assert sum(writes) == owner._input_bytes == size
    owner._release(size)


@pytest.mark.parametrize('limit', ['input', 'spool'])
def test_exact_limit_and_one_byte_over_with_multibyte_text(tmp_path, limit):
    value = {'text': '雪😀' * 10000}
    size = len(expected_bytes(value))
    for allowed in (size, size - 1):
        options = ({'_input_limit': allowed} if limit == 'input' else
                   {'_spool_limit': allowed + 1, '_output_limit': 1})
        owner = transport.ProcessProjector(tmp_path, **options)
        path = tmp_path / f'{limit}-{allowed}.input'
        if allowed == size:
            assert owner._encode(path, value)[0] == owner._input_bytes == size
            owner._release(size)
        else:
            with pytest.raises(transport._Limit, match=f'^{limit}_limit$'):
                owner._encode(path, value)
            assert not path.exists()
            assert owner._input_bytes == 0


@pytest.mark.parametrize('large', [False, True])
@pytest.mark.parametrize('invalid', [float('nan'), object(), '\ud800'])
def test_invalid_json_releases_only_owned_charged_bytes(tmp_path, large, invalid):
    owner = transport.ProcessProjector(tmp_path)
    owner._input_bytes = 37  # Another admitted input retains its reservation.
    path = tmp_path / 'invalid.input'
    value = {'a': 'x' * (130000 if large else 3), 'z': invalid}
    with pytest.raises((ValueError, TypeError, UnicodeError)):
        owner._encode(path, value)
    assert owner._input_bytes == 37
    assert not path.exists()


@pytest.mark.parametrize('fail_write', [1, 3])
def test_partial_file_error_restores_prior_reservation(tmp_path, monkeypatch, fail_write):
    owner = transport.ProcessProjector(tmp_path)
    owner._input_bytes = 53
    calls = []

    def fail(stream, data):
        calls.append(len(data))
        if len(calls) == fail_write:
            stream.write(data[:3])  # A real partially written artifact.
            stream.flush()
            raise OSError('synthetic disk write failure')

    observe_writes(monkeypatch, fail)
    path = tmp_path / 'partial.input'
    with pytest.raises(OSError, match='synthetic disk write failure'):
        owner._encode(path, {'text': 'x' * 200000})
    assert len(calls) == fail_write
    assert owner._input_bytes == 53
    assert not path.exists()


def test_stop_before_first_flush_drops_unreserved_pending_bytes(tmp_path, monkeypatch):
    owner = transport.ProcessProjector(tmp_path)
    owner._input_bytes = 29
    original = transport.json.JSONEncoder
    fragments = []

    class StopEncoder(original):
        def iterencode(self, value, *args, **kwargs):
            for chunk in super().iterencode(value, *args, **kwargs):
                fragments.append(chunk)
                if len(fragments) == 3:
                    owner._stop.set()
                yield chunk

    monkeypatch.setattr(transport.json, 'JSONEncoder', StopEncoder)
    writes = []
    observe_writes(monkeypatch, lambda stream, data: writes.append(len(data)))
    path = tmp_path / 'stopped.input'
    with pytest.raises(transport._Limit, match='^closed$'):
        owner._encode(path, {'rows': list(range(10000))})
    assert len(fragments) == 3 and not writes
    assert owner._input_bytes == 29
    assert not path.exists()


def test_stop_between_slices_of_one_large_json_fragment(tmp_path, monkeypatch):
    owner = transport.ProcessProjector(tmp_path)
    owner._input_bytes = 41
    writes = []

    def stop(stream, data):
        writes.append(len(data))
        if len(data) == transport._ENCODE_BATCH_BYTES:
            owner._stop.set()

    observe_writes(monkeypatch, stop)
    path = tmp_path / 'stopped-large.input'
    with pytest.raises(transport._Limit, match='^closed$'):
        owner._encode(path, {'text': '雪' * 100000})
    assert writes[-1] == transport._ENCODE_BATCH_BYTES and len(writes) == 2
    assert owner._input_bytes == 41
    assert not path.exists()


def test_concurrent_partial_encoding_cannot_release_or_exceed_another_reservation(tmp_path, monkeypatch):
    first = {'rows': ['a' * 100 for _ in range(900)]}
    second = {'text': 'b' * 80000}
    budget = len(expected_bytes(first))
    owner = transport.ProcessProjector(tmp_path, _spool_limit=budget + 1, _output_limit=1)
    entered, resume = threading.Event(), threading.Event()
    reservations = []

    def hold_first(stream, data):
        reservations.append(owner._input_bytes)
        if not entered.is_set():
            entered.set()
            assert resume.wait(3), 'Test did not release first encoding'

    observe_writes(monkeypatch, hold_first)
    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(owner._encode, tmp_path / 'first.input', first)
        try:
            assert entered.wait(3)
            charged = owner._input_bytes
            assert charged > 60000
            with pytest.raises(transport._Limit, match='^spool_limit$'):
                owner._encode(tmp_path / 'second.input', second)
            assert owner._input_bytes == charged
            assert not (tmp_path / 'second.input').exists()
        finally:
            resume.set()
        size, digest = active.result(timeout=3)
    assert max(reservations) <= budget
    assert size == owner._input_bytes == budget
    assert digest == hashlib.sha256(expected_bytes(first)).hexdigest()
    owner._release(size)
    assert owner._input_bytes == 0
