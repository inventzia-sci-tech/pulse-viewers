# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Following a run through the real viewer window while it is being written."""

import json

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from inventzia.pulse.viewers import event_viewer as ev
from inventzia.pulse.viewers.contract import event_record as er
from inventzia.pulse.viewers.contract import run_layout as rl

from conftest import WINDOW, counts


def pump(ms: int) -> None:
    """Let the Qt event loop run, so the follow timer actually fires."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle(win, predicate, tries=25, ms=120):
    for _ in range(tries):
        pump(ms)
        if predicate():
            return True
    return False


@pytest.fixture
def live_run(tmp_path):
    """A run in flight: manifest says running, recording has a header and one event."""
    root = tmp_path / "PulseOut"
    root.mkdir()
    paths = rl.create_run("REAL_TIME", "LiveApp", source="engine:test", window=WINDOW, root=root)
    rid = paths.dir.name
    with open(paths.events, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(rid, "REAL_TIME", *WINDOW)) + "\n")
        fh.write(json.dumps(er.event(rid, 0, WINDOW[0], "ext.bars", "AAPL", "T.Bar", {"i": 0}))
                 + "\n")

    run = dict(rl.read_manifest(paths.dir) or {})
    run.update({"dir": str(paths.dir), "_events_path": paths.events, "_has_manifest": True,
                "_tier": "live", "_events_bytes": paths.events.stat().st_size})
    return root, paths, rid, run


def append_event(paths, rid, seq, key="AAPL", type_id="T.Bar"):
    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.event(rid, seq, WINDOW[0] + seq, "ext.bars", key, type_id,
                                     {"i": seq})) + "\n")


def test_a_running_run_is_followed_automatically(qapp, live_run):
    root, paths, rid, run = live_run
    assert run["runStatus"] == "running"

    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    assert win.btn_follow.isChecked(), "opening a live run should start following it"
    assert win.model.rowCount() == 1

    append_event(paths, rid, 1)
    append_event(paths, rid, 2)
    assert settle(win, lambda: win.model.rowCount() == 3), f"rows={win.model.rowCount()}"


def test_new_events_reach_the_filters_and_stay_visible(qapp, live_run):
    """The seq bound must follow the data, or live events are filtered out of view unseen."""
    root, paths, rid, run = live_run
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    for seq in range(1, 8):
        append_event(paths, rid, seq, key=["AAPL", "MSFT", "GOOG"][seq % 3],
                     type_id=f"T.Type{seq % 4}")
    assert settle(win, lambda: win.model.rowCount() == 8)

    assert win.cb_key.count() >= 4, "new keys must join the filter"
    assert win.cb_type.count() >= 4, "new types must join the filter"
    assert win.seq_max.value() >= 7
    assert win.proxy.rowCount() == 8, "every arrived row must be visible through the proxy"


def test_a_user_set_bound_is_not_overwritten(qapp, live_run):
    root, paths, rid, run = live_run
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    # Let some data arrive first, so there is a range to choose a bound within.
    for seq in range(1, 6):
        append_event(paths, rid, seq)
    assert settle(win, lambda: win.model.rowCount() == 6)

    win.seq_max.setValue(2)                      # a deliberate choice by the user
    assert win.seq_max.value() == 2, "the bound must be settable within the range seen so far"

    append_event(paths, rid, 9)
    assert settle(win, lambda: win.model.rowCount() == 7), "rows keep arriving underneath"
    assert win.seq_max.value() == 2, "the viewer must not move a bound the user set"
    assert win.seq_max.maximum() >= 9, "but the range must grow, or the bound could never widen"


def test_pause_and_resume(qapp, live_run):
    root, paths, rid, run = live_run
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    win.set_following(False)
    paused_at = win.model.rowCount()
    append_event(paths, rid, 1)
    pump(700)
    assert win.model.rowCount() == paused_at, "a paused follow takes nothing up"
    assert "paused" in win.follow_state.text()

    win.set_following(True)
    assert settle(win, lambda: win.model.rowCount() == paused_at + 1), "resume must catch up"


def test_the_trailer_stops_the_follow(qapp, live_run):
    root, paths, rid, run = live_run
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.trailer(rid, 1, 1, 0, 0, 0, status="complete")) + "\n")

    assert settle(win, lambda: not win.btn_follow.isChecked()), "the recorder said it was done"
    assert "complete" in win.follow_state.text()


def test_a_finished_run_does_not_auto_follow_but_can_be_followed(qapp, live_run):
    root, paths, rid, run = live_run
    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.trailer(rid, 1, 1, 0, 0, 0, status="complete")) + "\n")
    rl.finalize_run(paths.dir, "completed", "complete", counts(1, 1))

    done = dict(run)
    done.update(rl.read_manifest(paths.dir))
    done["_events_path"] = paths.events

    viewer = ev.ViewerApp(str(root))
    viewer.open_run(done)
    win = viewer.windows[-1]

    assert not win.btn_follow.isChecked()
    assert win.model.rowCount() == 1
    win.set_following(True)
    assert win.btn_follow.isChecked(), "following a finished recording on demand is allowed"
