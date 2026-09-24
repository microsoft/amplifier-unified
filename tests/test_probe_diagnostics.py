import asyncio

from amplifier_web.probe_diagnostics import failure_detail, stderr_tail


async def test_stderr_retention_is_bounded_even_without_newlines():
    stream = asyncio.StreamReader()
    stream.feed_data(b'secret' * 100000 + b'final marker')
    stream.feed_eof()
    result = await stderr_tail(stream)
    assert len(result) == 32768
    assert result.endswith('final marker')


def test_unknown_stderr_never_echoes_private_details():
    result = failure_detail('Traceback /private/person/token abc-secret https://user:password@server.invalid')
    assert result == 'Check the runtime installation or update the app, then retry.'


def test_import_and_download_errors_have_distinct_next_steps():
    assert 'import a required module' in failure_detail('ModuleNotFoundError: private_name')
    assert 'DNS' in failure_detail('Could not resolve host: private.server')
    assert 'disk is full' in failure_detail('No space left on device /private/path')
