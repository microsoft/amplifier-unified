"""Keep discovery-process ownership across cancellation during creation."""
import asyncio
import os
import signal


async def _settle(task):
    """Finish the owned finalizer before propagating another cancellation."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _stop(process, readers=(), grace=0):
    if process.returncode is None:
        def send(force):
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif force:
                    process.kill()
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        if grace:
            send(False)
            try:
                await asyncio.wait_for(process.wait(), grace)
            except TimeoutError:
                send(True)
        else:
            send(True)
        await process.wait()
    if readers:
        await asyncio.gather(*readers, return_exceptions=True)


async def stop(process, *readers, grace=0):
    await _settle(asyncio.create_task(_stop(process, readers, grace)))


async def spawn(*command, **kwargs):
    # asyncio otherwise kills only the initial child if creation is cancelled
    # while pipe transports connect. Its already-started descendants survive.
    start = asyncio.create_task(asyncio.create_subprocess_exec(
        *command, **kwargs, start_new_session=True))
    try:
        return await asyncio.shield(start)
    except asyncio.CancelledError:
        async def finish_start():
            try:
                process = await start
            except OSError:
                return
            await _stop(process)
        await _settle(asyncio.create_task(finish_start()))
        raise
