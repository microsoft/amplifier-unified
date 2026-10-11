"""Synthetic parser fixtures, not playback evidence or the reporter's input."""
import hashlib
import os
import struct

import pytest

from amplifier_web.output_media import (
    MAX_BYTES, MediaRangeError, byte_range, read_verified, validate_media,
)


def gif(width=1, height=1, frames=2, delays=(7, 19), loop=1):
    # Two-entry palette and a complete one-pixel LZW stream. Larger logical
    # screens intentionally exercise composited-pixel admission, not decoding.
    header = b"GIF89a" + struct.pack("<HHBBB", width, height, 0x80, 0, 0)
    palette = b"\xff\x00\x00\x00\x00\xff"
    application = b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    rows = []
    for index in range(frames):
        rows.append(b"\x21\xf9\x04\x04" + struct.pack("<H", delays[index % len(delays)]) + b"\x00\x00")
        rows.append(b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00")
    return header + palette + application + b"".join(rows) + b"\x3b"


def atom(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def mp4():
    # Minimal structural fixture; deliberately not claimed as playable video.
    tkhd = bytearray(84)
    tkhd[76:84] = struct.pack(">II", 320 << 16, 240 << 16)
    hdlr = b"\x00" * 8 + b"vide" + b"\x00" * 12
    stsd = b"\x00" * 4 + struct.pack(">I", 1) + atom(b"avc1", b"\x00" * 78)
    mdia = atom(b"hdlr", hdlr) + atom(b"minf", atom(b"stbl", atom(b"stsd", stsd)))
    return atom(b"ftyp", b"isom\x00\x00\x00\x00isommp42") + atom(b"moov", atom(b"trak", atom(b"tkhd", bytes(tkhd)) + atom(b"mdia", mdia))) + atom(b"mdat", b"\x00")


def test_gif_metadata_preserves_nonuniform_delay_and_loop():
    data = gif()
    result = validate_media(data)
    assert result == {"mimeType": "image/gif", "width": 1, "height": 1,
                      "frameCount": 2, "durationMs": 260, "loopCount": 1,
                      "decodedPixelBytesEstimate": 8}
    assert data == gif()  # no rewriting or transcoding


@pytest.mark.parametrize("data", [
    b"", b"GIF89a", gif()[:-1], gif() + b"trailing",
    gif().replace(b"\x21\xf9\x04", b"\x21\xf9\x03", 1),
    gif().replace(b"\x02\x02\x44\x01\x00", b"\x01\x02\x44\x01\x00", 1),
    gif(width=0), gif(width=4097), gif(width=2048, height=2048),
    gif(frames=501), gif(width=1280, height=720, frames=37),
])
def test_gif_damaged_or_over_budget_refused(data):
    with pytest.raises(ValueError):
        validate_media(data)


def test_original_shape_estimate_admitted_without_native_memory_claim():
    result = validate_media(gif(width=1280, height=720, frames=26))
    assert result["decodedPixelBytesEstimate"] == 95_846_400
    assert result["frameCount"] == 26


def test_mp4_requires_sized_video_track_and_sample_description():
    result = validate_media(mp4())
    assert result["mimeType"] == "video/mp4"
    assert (result["width"], result["height"]) == (320, 240)
    for data in (mp4()[:-1], mp4().replace(b"vide", b"soun"),
                 mp4().replace(b"avc1", b"xxxx"), atom(b"ftyp", b"isom"),
                 b"\x00\x00\x00\x00moov", b"\x00\x00\x00\x01moov"):
        with pytest.raises(ValueError):
            validate_media(data)


def test_encoded_limit_remains_media_only():
    with pytest.raises(ValueError, match="32"):
        validate_media(b"x" * (MAX_BYTES + 1))
    from amplifier_web.outputs import MAX_FILE
    assert MAX_FILE == 8 * 1024 * 1024


def test_exact_regular_file_no_symlink_even_inside_workspace(tmp_path):
    data = gif()
    path = tmp_path / "clip.gif"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    saved, name, metadata = read_verified(tmp_path, "clip.gif", digest)
    assert saved == data and name == "clip.gif" and metadata["frameCount"] == 2
    (tmp_path / "alias.gif").symlink_to(path)
    (tmp_path / "dir").mkdir()
    (tmp_path / "alias-dir").symlink_to(tmp_path / "dir", target_is_directory=True)
    for value in ("alias.gif", "alias-dir/clip.gif", "../clip.gif", "."):
        with pytest.raises((ValueError, OSError)):
            read_verified(tmp_path, value, digest)
    with pytest.raises(ValueError, match="SHA"):
        read_verified(tmp_path, "clip.gif", "0" * 64)


def test_file_modified_during_read_refused(tmp_path, monkeypatch):
    path = tmp_path / "clip.gif"
    path.write_bytes(gif())
    real = os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(gif(frames=3))
        return real(fd)

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(ValueError, match="changed"):
        read_verified(tmp_path, "clip.gif", hashlib.sha256(gif()).hexdigest())


@pytest.mark.parametrize("header, expected", [
    (None, None), ("bytes=2-4", (2, 4)), ("bytes=2-", (2, 9)),
    ("bytes=-3", (7, 9)), ("bytes=-30", (0, 9)), ("bytes=0-99", (0, 9)),
])
def test_single_exact_byte_range(header, expected):
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header, status", [
    ("bytes=10-", 416), ("bytes=4-2", 416), ("bytes=-0", 416),
    ("bytes=0-1,3-4", 400), ("items=0-1", 400), ("bytes=-", 400),
    ("bytes= 0-1", 400), ("bytes=" + "9" * 100 + "-", 400),
])
def test_range_refusal_policy_explicit(header, status):
    with pytest.raises(MediaRangeError) as error:
        byte_range(header, 10)
    assert error.value.status == status