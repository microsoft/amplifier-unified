"""Dependency-free synthetic pixels for the isolated inline-media host.

Not a parser stub: clear-code LZW streams encode every supplied pixel. Public
qualification may instead supply original synthetic/public bytes via env.
"""
import struct
import zlib
import random


COLORS = ((0, 0, 0, 0), (255, 0, 0, 255), (0, 0, 255, 255), (0, 255, 0, 255))


def blocks(data):
    return b''.join(bytes([len(data[pos:pos + 255])]) + data[pos:pos + 255]
                    for pos in range(0, len(data), 255)) + b'\0'


def lzw(pixels, minimum=2, *, pair_tail=False):
    # Frequent clears keep the code width fixed. The last noise frame may
    # encode pairs after its first two thirds (still below a width increase).
    # Every code represents real image pixels, never comment/file padding.
    output = bytearray()
    accumulator = bits = 0
    width = minimum + 1
    clear = 1 << minimum
    for index, pixel in enumerate(pixels):
        pair = pair_tail and index >= len(pixels) * 2 // 3 and index % 2 == 1
        for code in (pixel,) if pair else (clear, pixel):
            accumulator |= code << bits
            bits += width
            if bits >= 8:
                output.append(accumulator & 255)
                accumulator >>= 8
                bits -= 8
    accumulator |= (clear + 1) << bits
    bits += width
    while bits > 0:
        output.append(accumulator & 255)
        accumulator >>= 8
        bits -= 8
    return bytes(output)


def animation(width=4, height=2, count=3, *, partial=False, noise=False, delay_cs=None):
    palette = COLORS + ((255, 255, 0, 255), (0, 255, 255, 255), (255, 0, 255, 255), (255, 255, 255, 255)) if noise else COLORS
    data = bytearray(b'GIF89a' + struct.pack('<HHBBB', width, height, 0x82 if noise else 0x81, 0, 0))
    data.extend(bytes(component for rgba in palette for component in rgba[:3]))
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
    rng = random.Random(319)
    for index, (x, y, w, h, pixels, disposal) in enumerate(frames):
        minimum = 3 if noise and index < 5 else 2
        if noise:
            pixels = bytes(value & ((1 << minimum) - 1) for value in rng.randbytes(w * h))
        delay = delay_cs if delay_cs is not None else (40, 120, 70, 90)[index % 4]
        data.extend(b'\x21\xf9\x04' + bytes([(disposal << 2) | 1]) +
                    struct.pack('<H', delay) + b'\0\0')
        data.extend(b'\x2c' + struct.pack('<HHHHB', x, y, w, h, 0) + bytes([minimum]) +
                    blocks(lzw(pixels, minimum, pair_tail=noise and index == 4)))
    data.append(0x3b)
    return bytes(data), [[list(COLORS[pixel]) for pixel in frame] for frame in expected]


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 4) * 2)) + chunk(b'IEND', b''))