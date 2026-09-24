# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Defects found by review, each pinned so it cannot come back.

Every one of these is a case where the viewer looked fine while telling the reader something untrue —
the failure mode the whole health story exists to prevent.
"""

import json

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from inventzia.pulse.viewers import event_viewer as ev
from inventzia.pulse.viewers import run_browser as rb
from inventzia.pulse.viewers.contract import event_record as er
from inventzia.pulse.viewers.contract import run_layout as rl

from conftest import WINDOW, counts


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle(predicate, tries=40, ms=100) -> bool:
    for _ in range(tries):
        pump(ms)
        if predicate():
            return True
    return False


# ---------------------------------------------------------------------------
# 1. A recording that contradicts itself must never read as healthy.
# ---------------------------------------------------------------------------

@pytest.fixture
def lying_recording(tmp_path):
    """One event, but a trailer claiming two: the file disagrees with itself."""
    path = tmp_path / "events.jsonl"
    rid = "liar"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(rid, "COMPRESSED_TIME", *WINDOW)) + "\n")
        fh.write(json.dumps(er.event(rid, 0, WINDOW[0], "ext.bars", "K", "T", {"i": 0})) + "\n")
        fh.write(json.dumps(er.trailer(rid, 2, 2, 0, 0, 0, status="complete")) + "\n")
    return path


def test_validation_failure_is_visible_in_the_verdict(qapp, lying_recording):
    report = er.validate(lying_recording)
    assert not report.ok and report.diagnostics, "the fixture must actually be invalid"

    win = ev.MainWindow(str(lying_recording), None, None)

    text = win.health.text()
    # Match the badge itself: "OK" is a substring of "BROKEN".
    assert "<b>OK</b>" not in text, f"an invalid recording must not read as OK: {text}"
    assert "<b>BROKEN</b>" in text, text
    assert "contract" in text or "unreliable" in text, text


def test_health_downgrades_a_clean_manifest_when_the_file_disagrees():
    """The manifest is not evidence about the file's contents."""
    healthy = {"runStatus": "completed", "recordingStatus": "complete",
               "counts": counts(2, 2), "_has_manifest": True}

    assert rb.health(healthy)[0] == rb.OK
    verdict, why = rb.health(healthy, validation_issues=1)
    assert verdict == rb.BROKEN
    assert "1 validation issue" in why


# ---------------------------------------------------------------------------
# 2. A followed run must not keep showing RUNNING once it has finished.
# ---------------------------------------------------------------------------

@pytest.fixture
def in_flight(tmp_path):
    root = tmp_path / "PulseOut"
    root.mkdir()
    paths = rl.create_run("REAL_TIME", "LiveApp", source="engine:test", window=WINDOW, root=root)
    rid = paths.dir.name
    with open(paths.events, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(rid, "REAL_TIME", *WINDOW)) + "\n")
        fh.write(json.dumps(er.event(rid, 0, WINDOW[0], "ext.bars", "K", "T", {"i": 0})) + "\n")
    run = dict(rl.read_manifest(paths.dir) or {})
    run.update({"dir": str(paths.dir), "_events_path": paths.events, "_has_manifest": True,
                "_tier": "live", "_events_bytes": paths.events.stat().st_size})
    return root, paths, rid, run


def _finish(paths, rid, run_status="failed", rec_status="complete", cnt=None):
    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.trailer(rid, 1, 1, 0, 0, 0, status=rec_status)) + "\n")
    rl.finalize_run(paths.dir, run_status, rec_status, cnt or counts(1, 1))


def test_a_finished_run_stops_showing_running(qapp, in_flight):
    root, paths, rid, run = in_flight
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]
    assert win.btn_follow.isChecked()
    assert "<b>RUNNING</b>" in win.health.text(), win.health.text()

    _finish(paths, rid, run_status="failed")

    assert settle(lambda: not win.btn_follow.isChecked()), "the trailer should stop the follow"
    assert settle(lambda: "<b>RUNNING</b>" not in win.health.text()), \
        f"a finished run must not still read as running: {win.health.text()}"
    assert "<b>BROKEN</b>" in win.health.text(), win.health.text()
    assert "1 recorded" in win.health.text(), "the final counts should be shown, not omitted"


def test_finalization_arriving_after_the_trailer_is_picked_up(qapp, in_flight):
    """The recorder stops first; the launcher finalizes the manifest a moment later."""
    root, paths, rid, run = in_flight
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    # Trailer only: the recording is done, but the manifest still says running.
    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.trailer(rid, 1, 1, 0, 0, 0, status="complete")) + "\n")
    assert settle(lambda: not win.btn_follow.isChecked())
    assert win._finalize_timer.isActive(), "it should be watching for the manifest to settle"

    # Now the launcher finalizes, after the follow has already stopped.
    rl.finalize_run(paths.dir, "completed", "complete", counts(1, 1))

    assert settle(lambda: "<b>OK</b>" in win.health.text()), \
        f"the late manifest should be picked up: {win.health.text()}"
    assert not win._finalize_timer.isActive(), "and the watch should stop once it has"


def test_the_finalization_watch_gives_up(qapp, in_flight, monkeypatch):
    """A manifest that never settles must not leave a timer running for ever."""
    monkeypatch.setattr(ev, "FINALIZE_TIMEOUT_SECONDS", 0.5)
    root, paths, rid, run = in_flight
    viewer = ev.ViewerApp(str(root))
    viewer.open_run(run)
    win = viewer.windows[-1]

    with open(paths.events, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(er.trailer(rid, 1, 1, 0, 0, 0, status="complete")) + "\n")

    assert settle(lambda: not win.btn_follow.isChecked())
    assert settle(lambda: not win._finalize_timer.isActive(), tries=30), \
        "the watch must time out rather than poll for ever"


# ---------------------------------------------------------------------------
# 3. Retention must bound an oversized batch, not only accumulated rows.
# ---------------------------------------------------------------------------

def _events(n):
    return [er.event("r", i, WINDOW[0] + i, "t", "k", "T", {"i": i}) for i in range(n)]


def test_a_single_oversized_batch_is_trimmed(qapp):
    model = ev.EventTableModel(max_rows=3)
    model.append_events(_events(5))
    assert model.rowCount() == 3, "one batch must not exceed the retention bound"


def test_the_most_recent_rows_are_the_ones_kept(qapp):
    model = ev.EventTableModel(max_rows=3)
    model.append_events(_events(5))
    kept = [model.data(model.index(r, 0), ev.RAW_ROLE)["seq"] for r in range(model.rowCount())]
    assert kept == [2, 3, 4], f"retention keeps the newest, got {kept}"


def test_retention_holds_across_several_batches(qapp):
    model = ev.EventTableModel(max_rows=4)
    for _ in range(3):
        model.append_events(_events(3))
    assert model.rowCount() == 4


def test_an_exactly_sized_batch_is_untouched(qapp):
    model = ev.EventTableModel(max_rows=3)
    model.append_events(_events(3))
    assert model.rowCount() == 3
