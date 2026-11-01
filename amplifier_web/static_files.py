"""Bounded bundled-file delivery without asyncio's tiny TLS fallback reads."""
from aiohttp import web


# A transfer buffer, not an asset-size limit or a retained cache. At the current
# client size this needs three reads instead of asyncio's ~176 16 KiB reads.
TRANSFER_BYTES = 1024 * 1024


class BundledFileResponse(web.FileResponse):
    def __init__(self, path, **kwargs):
        super().__init__(path, chunk_size=TRANSFER_BYTES, **kwargs)

    async def _sendfile(self, request, fobj, offset, count):
        transport = request.transport
        if transport is None or transport.get_extra_info('sslcontext') is None:
            return await super()._sendfile(request, fobj, offset, count)
        # TLS cannot use the OS sendfile path. asyncio's fallback reads 16 KiB
        # per executor round trip, magnifying a busy host loop into minutes of
        # startup delay. Use aiohttp's bounded, backpressured file writer; its
        # parent still owns ranges, validators, HEAD, file opening and closure.
        writer = await web.StreamResponse.prepare(self, request)
        return await self._sendfile_fallback(writer, fobj, offset, count)


class BundledStaticResource(web.StaticResource):
    async def _handle(self, request):
        response = await super()._handle(request)
        if isinstance(response, web.FileResponse):
            # Retain aiohttp's canonical traversal/symlink/directory checks.
            # _path and _sendfile are the two aiohttp file-response extension
            # points covered by the real HTTP/TLS delivery regression tests.
            return BundledFileResponse(response._path, headers=response.headers,
                                       status=response.status)
        return response
