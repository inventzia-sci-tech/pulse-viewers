# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Every coloured cell stays readable under both a light and a dark desktop theme.

Guards a real bug: setting a cell's background but leaving its text colour to the theme produces
light-on-light (or dark-on-dark) the moment the theme flips, which made the run list unreadable.
Measured as a WCAG contrast ratio rather than by eye.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from inventzia.pulse.viewers import event_viewer as ev
from inventzia.pulse.viewers import run_browser as rb

AA = 4.5          # WCAG AA for normal text


def _luminance(c: QColor) -> float:
    def chan(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(c.red()) + 0.7152 * chan(c.green()) + 0.0722 * chan(c.blue())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.fixture
def theme(qapp, request):
    """Install a light or dark palette for the duration of a test."""
    dark = request.param
    previous = qapp.palette()
    pal = QPalette()
    ground = QColor(0x1E, 0x1E, 0x1E) if dark else QColor(0xFF, 0xFF, 0xFF)
    text = QColor(0xFF, 0xFF, 0xFF) if dark else QColor(0x00, 0x00, 0x00)
    for role in (QPalette.Window, QPalette.Base):
        pal.setColor(role, ground)
    for role in (QPalette.WindowText, QPalette.Text):
        pal.setColor(role, text)
    qapp.setPalette(pal)
    yield dark
    qapp.setPalette(previous)


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
def test_theme_is_detected(theme):
    assert rb.palette_is_dark() == theme


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
@pytest.mark.parametrize("verdict", [rb.OK, rb.PARTIAL, rb.BROKEN, rb.RUNNING, rb.UNKNOWN])
def test_verdict_colours_are_readable(theme, verdict):
    bg, fg = rb.verdict_colors(verdict)
    assert contrast(QColor(*bg), QColor(*fg)) >= AA


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
@pytest.mark.parametrize("tier", ["historical", "live"])
def test_tier_colours_are_readable(theme, tier):
    bg, fg = rb.tier_colors(tier)
    assert contrast(QColor(*bg), QColor(*fg)) >= AA


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
def test_tiers_are_visually_distinct(theme):
    assert rb.tier_colors("historical")[0] != rb.tier_colors("live")[0]


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
def test_generated_type_colours_are_readable(theme):
    """The event grid colours by typeId, so the whole generated range must hold up."""
    worst = min(contrast(ev.type_color(f"com.inventzia.pulse.data.schemas.Type{i}"),
                         ev.type_text_color())
                for i in range(400))
    assert worst >= AA, f"worst generated type colour was {worst:.2f}"


@pytest.mark.parametrize("theme", [False, True], ids=["light", "dark"], indirect=True)
def test_only_meaningful_columns_are_painted_and_always_in_pairs(theme, runs_root):
    """A cell with a background must set its foreground too, and vice versa."""
    dlg = rb.make_browser(runs_root)
    painted = set()
    for row in range(dlg.table.rowCount()):
        for col in range(dlg.table.columnCount()):
            item = dlg.table.item(row, col)
            has_bg = item.background().style() != Qt.NoBrush
            has_fg = item.foreground().style() != Qt.NoBrush
            assert has_bg == has_fg, f"row {row} col {col}: colours must be set as a pair"
            if has_bg:
                painted.add(col)
                assert contrast(item.background().color(),
                                item.foreground().color()) >= AA
    assert painted == {rb.COL_TIER, rb.COL_HEALTH}, \
        "only the tier and the verdict carry meaning; colouring more is noise"
