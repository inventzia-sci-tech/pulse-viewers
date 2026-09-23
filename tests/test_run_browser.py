# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""The run browser: listing, selecting, opening, and refusing to open what cannot be opened."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialogButtonBox

from inventzia.pulse.viewers import run_browser as rb


@pytest.fixture(autouse=True)
def forget_remembered_root(qapp):
    """The remembered output folder is real user state; never let it leak between tests."""
    QSettings(rb._ORG, rb._APP).clear()
    yield
    QSettings(rb._ORG, rb._APP).clear()


def test_lists_every_run_including_damaged_ones(qapp, runs_root):
    dlg = rb.make_browser(runs_root)
    assert dlg.table.rowCount() == 8
    assert rb.COLUMNS[0] == "tier" and rb.COLUMNS[-1] == "health"


def test_selecting_a_run_enables_open_and_explains_it(qapp, runs_root):
    dlg = rb.make_browser(runs_root)
    opened = 0
    for row in range(dlg.table.rowCount()):
        dlg.table.selectRow(row)
        run = dlg.current_run()
        assert dlg.detail.text(), "a selected run must be described"
        if run["_events_bytes"] > 0:
            assert dlg.buttons.button(QDialogButtonBox.Open).isEnabled()
            opened += 1
        else:
            assert not dlg.buttons.button(QDialogButtonBox.Open).isEnabled()
    assert opened >= 6


def test_a_run_with_no_events_cannot_be_opened(qapp, runs_root):
    dlg = rb.make_browser(runs_root)
    for row in range(dlg.table.rowCount()):
        dlg.table.selectRow(row)
        if dlg.current_run()["_events_bytes"] < 0:
            dlg.open_current()
            assert dlg.chosen is None, "offering a file that is not there would be a lie"
            return
    pytest.fail("fixture corpus should contain a run with no events file")


def test_opening_hands_the_run_over_and_keeps_the_browser(qapp, runs_root):
    """The browser is a hub, not a one-shot picker."""
    handed = []
    dlg = rb.make_browser(runs_root, on_open=handed.append)
    assert not dlg.isModal()
    for row in range(dlg.table.rowCount()):
        dlg.table.selectRow(row)
        if dlg.current_run()["_events_bytes"] > 0:
            break
    dlg.open_current()
    assert len(handed) == 1
    assert dlg.chosen is handed[0]


def test_empty_root_explains_itself(qapp, empty_root):
    """An empty list with a disabled button tells the user nothing."""
    dlg = rb.make_browser(empty_root)
    assert dlg.table.rowCount() == 0
    assert "Change output folder" in dlg.detail.text()
    assert not dlg.buttons.button(QDialogButtonBox.Open).isEnabled()


def test_missing_root_does_not_crash(qapp, tmp_path):
    dlg = rb.make_browser(tmp_path / "nope")
    assert dlg.table.rowCount() == 0
    assert "does not exist" in dlg.detail.text()


def test_root_precedence_explicit_then_remembered_then_environment(qapp, runs_root, empty_root,
                                                                  monkeypatch):
    monkeypatch.setenv("PULSE_OUTPUT", str(empty_root))
    assert rb.resolve_root(None) == empty_root, "environment applies when nothing is remembered"

    QSettings(rb._ORG, rb._APP).setValue(rb._ROOT_KEY, str(runs_root))
    assert rb.resolve_root(None) == runs_root, "a folder chosen in the UI outranks the environment"
    assert rb.resolve_root(empty_root) == empty_root, "an explicit root outranks everything"
