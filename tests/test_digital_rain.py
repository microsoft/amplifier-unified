"""The included animated appearance stays self-contained and layout-neutral."""
import base64
import re
import struct
from importlib.resources import files

import pytest

from amplifier_web.service import validate_theme
from test_canvas_apps import app, dispatch


def source():
    return files("amplifier_web").joinpath("appearances/digital-rain.amplifier.css").read_text()


def test_digital_rain_is_portable_and_has_three_original_tiles():
    css = source()
    validate_theme(css)
    assert len(css.encode()) < 100_000
    tiles = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", css)
    assert len(tiles) == 3
    dimensions = []
    for tile in tiles:
        data = base64.b64decode(tile, validate=True)
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        dimensions.append(struct.unpack(">II", data[16:24]))
    assert dimensions == [(512, 768), (704, 960), (896, 1152)]
    readable = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", css)
    assert not re.search(r"\b(matrix|tron|kitt)\b", readable, re.I)
    assert not re.search(r"https?://|@import|@font-face", readable)
    assert "digital-rain-drift 72s linear infinite" in css
    assert 'data-decorations="off"' in css
    assert "prefers-reduced-motion:reduce" in css
    assert "forced-colors:active" in css
    assert not re.search(r'conversation["\']?\]?\s*>\s*\*', css)


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))


@pytest.mark.parametrize("ink,surface", [
    ("#143522", "#f8fcf9"), ("#486652", "#e8f3ec"),
    ("#ffffff", "#176c3b"), ("#143522", "#d6ebdd"),
    ("#e0f4e5", "#06100b"), ("#a0bdab", "#0b1a12"),
    ("#03200c", "#85eea2"),
])
def test_digital_rain_text_palette_contrast(ink, surface):
    css = source()
    assert ink in css and surface in css
    a, b = sorted((luminance(ink), luminance(surface)))
    assert (b + .05) / (a + .05) >= 4.5


async def test_included_digital_rain_can_be_read_saved_and_applied(app):
    initial = dict(app.state["theme"])
    listing = (await dispatch(app, "theme.list", {}))["result"]
    row = next(item for item in listing["items"] if item["id"] == "builtin:digital-rain")
    assert row["name"] == "Digital Rain" and row["source"] == "built-in"
    assert "css" not in row
    theme = (await dispatch(app, "theme.read", {"id": row["id"]}))["result"]
    assert theme["css"] == source()
    validate_theme(theme["css"])
    saved = (await dispatch(app, "theme.save", {
        "name": "My rain", "css": theme["css"],
    }))["result"]
    assert saved["id"].startswith("saved:")
    await dispatch(app, "theme.preview", {"name": theme["name"], "css": theme["css"]})
    assert app.state["theme"] == initial
    await dispatch(app, "theme.revert", {})
    await dispatch(app, "theme.apply", {"name": theme["name"], "css": theme["css"]})
    assert app.state["theme"]["name"] == "Digital Rain"
    assert app.state["theme"]["css"] == theme["css"]