# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Window management: the browser is a standing hub, each run gets its own detached window."""

import pytest

from inventzia.pulse.viewers import event_viewer as ev
from inventzia.pulse.viewers import run_browser as rb


@pytest.fixture
def openable(runs_root):
    runs = [r for r in rb.load_runs(runs_root) if r["_events_bytes"] > 0]
    assert len(runs) >= 3, "need several openable runs"
    return runs


def test_empty_window_points_at_the_browser(qapp, runs_root):
    win = ev.MainWindow(None, None, str(runs_root))
    win.show()
    assert win.isVisible()
    assert win.model.rowCount() == 0
    assert "Open run" in win.meta.text()
    assert not win.health.isVisible(), "no verdict before a run is loaded"


def test_each_run_opens_in_its_own_window(qapp, runs_root, openable):
    viewer = ev.ViewerApp(str(runs_root))
    viewer.show_browser()
    assert viewer.browser.isVisible()
    assert not viewer.browser.isModal(), "a modal browser would block the viewers it opens"

    for run in openable[:3]:
        viewer.open_run(run)
        assert viewer.browser.isVisible(), "opening a run must not close the browser"

    assert len(viewer.windows) == 3
    titles = [w.windowTitle() for w in viewer.windows]
    assert len(set(titles)) == 3, f"windows must be distinguishable: {titles}"
    assert all(w.isVisible() for w in viewer.windows)
    assert all(rb.APP_NAME in t for t in titles)


def test_closing_one_window_leaves_the_others_and_the_browser(qapp, runs_root, openable):
    viewer = ev.ViewerApp(str(runs_root))
    viewer.show_browser()
    for run in openable[:2]:
        viewer.open_run(run)

    first, last = viewer.windows[0], viewer.windows[-1]
    first.close()

    assert not first.isVisible()
    assert last.isVisible()
    assert viewer.browser.isVisible()


def test_closing_the_browser_leaves_the_viewers_and_it_can_be_summoned_back(qapp, runs_root,
                                                                           openable):
    viewer = ev.ViewerApp(str(runs_root))
    viewer.show_browser()
    viewer.open_run(openable[0])
    win = viewer.windows[-1]

    viewer.browser.close()
    assert not viewer.browser.isVisible()
    assert win.isVisible()

    win.browse_runs()
    assert viewer.browser.isVisible()


def test_loading_another_run_replaces_contents_not_the_window(qapp, runs_root, openable):
    viewer = ev.ViewerApp(str(runs_root))
    win = viewer.open_path(str(openable[0]["_events_path"]), openable[0])
    identity, title = id(win), win.windowTitle()

    win.load(str(openable[1]["_events_path"]), openable[1])

    assert id(win) == identity
    assert win.isVisible()
    assert win.windowTitle() != title, "the title follows the loaded run"


def test_a_loose_recording_takes_its_health_from_the_trailer(qapp, tmp_path):
    """Opened outside the output layout, there is no manifest to read."""
    from inventzia.pulse.viewers.contract import event_record as er

    bare = tmp_path / "bare.jsonl"
    er.make_sample(bare, events=12)
    win = ev.MainWindow(str(bare), None, None)
    assert win.model.rowCount() == 12
    assert win.health.text(), "a recording with a trailer still has a verdict"
