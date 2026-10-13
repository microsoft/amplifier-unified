"""Reproducible ~60 MB, 1920x802, 68-frame GIF; every byte encodes pixels.

Uses deliberately simple clear-code groups rather than padding or thumbnails.
Run in the isolated test environment with an output path. Pillow can separately
validate every composited frame; native browser decoding is qualified separately.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
from inline_media_samples import blocks


def write(path):
    width, height, frames = 1920, 802, 68
    with Path(path).open('wb') as stream:
        stream.write(b'GIF89a' + struct.pack('<HHBBB', width, height, 0x82, 0, 0))
        stream.write(bytes([0,0,0,255,0,0,0,255,0,0,0,255,255,255,0,0,255,255,255,0,255,255,255,255]))
        stream.write(b'\x21\xff\x0bNETSCAPE2.0\x03\x01\0\0\0')
        for index in range(frames):
            minimum = 3 if index < 4 else 2
            bits, color = minimum + 1, index % 3 + 1
            # Eight pairs end on a byte boundary (72 or 96 bits).
            group = sum(code << (bits * offset) for offset, code in
                        enumerate([(1 << minimum), color, color] * 8))
            encoded = group.to_bytes(bits * 3, 'little') * (width * height // 16)
            encoded += ((1 << minimum) + 1).to_bytes(1, 'little')
            stream.write(b'\x21\xf9\x04\x04' + struct.pack('<H', 7 if index < 46 else 6) + b'\0\0')
            stream.write(b'\x2c' + struct.pack('<HHHHB', 0, 0, width, height, 0) + bytes([minimum]) + blocks(encoded))
        stream.write(b'\x3b')
    data = Path(path).read_bytes()
    print(json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                      'width':width,'height':height,'frames':frames,'durationMs':4540}))


if __name__ == '__main__':
    write(sys.argv[1])
