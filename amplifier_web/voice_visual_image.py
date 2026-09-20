"""Bounded validation for browser canvas PNG snapshots; no native capture."""
import struct
import zlib


def validate_png(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 500000:
        raise ValueError('Use a PNG frame up to 500 KB.')
    offset, width, height, channels = 8, 0, 0, 0
    compressed = bytearray()
    while offset+12 <= len(data):
        size = int.from_bytes(data[offset:offset+4], 'big')
        kind = data[offset+4:offset+8]
        end = offset+size+12
        if end > len(data):
            break
        body = data[offset+8:end-4]
        if zlib.crc32(kind+body) != int.from_bytes(data[end-4:end], 'big'):
            break
        if not width:
            if kind != b'IHDR' or size != 13:
                break
            width,height,depth,color,compression,filtering,interlace = struct.unpack('>IIBBBBB',body)
            if not 1 <= width <= 1280 or not 1 <= height <= 1280 or depth != 8 or color not in (2,6) or compression or filtering or interlace:
                break
            channels = 3 if color == 2 else 4
        elif kind == b'IHDR':
            break
        if kind == b'IDAT':
            compressed.extend(body)
        if kind == b'IEND':
            if size or end != len(data) or not compressed:
                break
            expected = (width*channels+1)*height
            decoder = zlib.decompressobj()
            try:
                pixels = decoder.decompress(bytes(compressed),expected+1)
            except zlib.error:
                break
            if (len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                    or any(pixels[row*(width*channels+1)] > 4 for row in range(height))):
                break
            return width,height
        offset = end
    raise ValueError('The frame is not a complete browser PNG image.')
