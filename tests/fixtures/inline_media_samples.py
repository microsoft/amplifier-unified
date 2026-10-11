"""Dependency-free synthetic pixels for the isolated inline-media host.

Not a parser stub: clear-code LZW streams encode every supplied pixel. Public
qualification may instead supply original synthetic/public bytes via env.
"""
import struct
import zlib


COLORS = ((0, 0, 0, 0), (255, 0, 0, 255), (0, 0, 255, 255), (0, 255, 0, 255))


def blocks(data):
    return b''.join(bytes([len(data[pos:pos + 255])]) + data[pos:pos + 255]
                    for pos in range(0, len(data), 255)) + b'\0'


def lzw(pixels):
    # Clear before each pixel keeps the code width at three bits. This trades
    # compression for an easily audited, actual full-frame fixture.
    output = bytearray()
    accumulator = bits = 0
    for pixel in pixels:
        for code in (4, pixel):
            accumulator |= code << bits
            bits += 3
            if bits >= 8:
                output.append(accumulator & 255)
                accumulator >>= 8
                bits -= 8
    accumulator |= 5 << bits
    bits += 3
    while bits > 0:
        output.append(accumulator & 255)
        accumulator >>= 8
        bits -= 8
    return bytes(output)


def animation(width=4, height=2, count=3, *, partial=False, target_bytes=None):
    data = bytearray(b'GIF89a' + struct.pack('<HHBBB', width, height, 0x81, 0, 0))
    data.extend(bytes(component for rgba in COLORS for component in rgba[:3]))
    data.extend(b'\x21\xff\x0bNETSCAPE2.0\x03\x01\0\0\0')  # infinite repeat
    expected = []
    if partial:
        frames = [(0, 0, 4, 2, [1] * 8, 1),
                  (0, 0, 2, 2, [2, 0, 0, 2], 2),
                  (2, 0, 2, 2, [3] * 4, 3),
                  (0, 0, 1, 1, [2], 1)]
        expected = [[1] * 8, [2, 1, 1, 1, 1, 2, 1, 1],
                    [0, 0, 3, 3, 0, 0, 3, 3], [2, 0, 1, 1, 0, 0, 1, 1]]
    else:
        frames = [(0, 0, width, height, bytes([index % 3 + 1]) * (width * height), 1)
                  for index in range(count)]
    for index, (x, y, w, h, pixels, disposal) in enumerate(frames):
        delay = (40, 120, 70, 90)[index % 4]
        data.extend(b'\x21\xf9\x04' + bytes([(disposal << 2) | 1]) +
                    struct.pack('<H', delay) + b'\0\0')
        data.extend(b'\x2c' + struct.pack('<HHHHB', x, y, w, h, 0) + b'\x02' + blocks(lzw(pixels)))
    if target_bytes is not None:
        # Comment padding has its own byte overhead; fill exactly, no trailing
        # garbage after the trailer. Images above still encode all 26 screens.
        remaining = target_bytes - len(data) - 1
        if remaining < 3:
            raise ValueError('Synthetic image data exceeds requested encoded size')
        data.extend(b'\x21\xfe')
        remaining -= 3  # label and terminator
        while remaining:
            count = min(255, remaining - 1)
            if count < 1:
                # Empty comment extension is three bytes, avoid a one-byte gap.
                raise ValueError('Cannot pad this encoded size exactly')
            if remaining - count - 1 == 1:
                count -= 1
            data.extend(bytes([count]) + b'x' * count)
            remaining -= count + 1
        data.append(0)
    data.append(0x3b)
    return bytes(data), [[list(COLORS[pixel]) for pixel in frame] for frame in expected]


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 4) * 2)) + chunk(b'IEND', b''))