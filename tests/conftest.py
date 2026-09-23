# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Shared fixtures.

The important one is `runs_root`: a corpus of deliberately *damaged* runs. A viewer's job is to
open the run someone is worried about, so the states worth testing are the broken ones — a crashed
engine, a failed recorder, a lossy capture, counts that disagree with themselves, a run that never
finalized, one with no manifest, one with no events at all. Two healthy runs would prove very
little.
"""

from __future__ import annotations

import json
import os

import pytest

from inventzia.pulse.viewers.contract import event_record as er
from inventzia.pulse.viewers.contract import run_layout as rl

WINDOW = (1_283_630_000_000, 1_283_630_005_000)

# Qt must run headless in CI; set before any QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def counts(observed, events, overflow=0, ser=0, abandoned=0) -> dict:
    return {"observed": observed, "events": events, "overflow": overflow,
            "serializationErrors": ser, "abandoned": abandoned}


def write_recording(path, run_id, n_events, status="complete", cnt=None) -> None:
    """A valid recording: header, n events, trailer."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(run_id, "COMPRESSED_TIME", *WINDOW)) + "\n")
        for i in range(n_events):
            fh.write(json.dumps(er.event(run_id, i, WINDOW[0] + i, "ext.bars", "K",
                                         "com.inventzia.pulse.ext.ExtendedBar", {"i": i})) + "\n")
        c = cnt or counts(n_events, n_events)
        fh.write(json.dumps(er.trailer(run_id, c["observed"], c["events"], c["overflow"],
                                       c["serializationErrors"], c["abandoned"],
                                       status=status)) + "\n")


def make_run(root, app, mode, n_events, run_status, rec_status, cnt,
             *, write_events=True, finalize=True):
    paths = rl.create_run(mode, app, source="engine:test", window=WINDOW, root=root)
    if write_events:
        rec_status_for_trailer = rec_status if rec_status in ("complete", "failed", "interrupted") \
            else "complete"
        write_recording(paths.events, paths.dir.name, n_events, rec_status_for_trailer, cnt)
    if finalize:
        rl.finalize_run(paths.dir, run_status, rec_status, cnt)
    return paths


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the session; Qt allows only one."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def empty_root(tmp_path):
    # A distinct name from `runs_root`, so a test may ask for both.
    root = tmp_path / "EmptyOut"
    root.mkdir()
    return root


@pytest.fixture
def runs_root(tmp_path):
    """Eight runs covering the health verdicts a viewer must render honestly."""
    root = tmp_path / "PulseOut"
    root.mkdir()
    made = {}

    made["clean"] = make_run(root, "CleanApp", "COMPRESSED_TIME", 3,
                             "completed", "complete", counts(3, 3))
    made["crashed"] = make_run(root, "CrashedApp", "COMPRESSED_TIME", 1,
                               "failed", "complete", counts(3, 1, abandoned=2))
    made["bad_recorder"] = make_run(root, "BadRecorderApp", "REAL_TIME", 0,
                                    "completed", "failed", counts(5, 0, abandoned=5))
    made["lossy"] = make_run(root, "LossyApp", "REAL_TIME", 8,
                             "completed", "complete", counts(500, 8, overflow=492))
    made["unbalanced"] = make_run(root, "InconsistentApp", "COMPRESSED_TIME", 2,
                                  "completed", "complete", counts(9, 2))
    made["running"] = make_run(root, "StillRunningApp", "REAL_TIME", 2,
                               "completed", "complete", None, finalize=False)
    no_manifest = make_run(root, "NoManifestApp", "COMPRESSED_TIME", 2,
                           "completed", "complete", counts(2, 2))
    (no_manifest.dir / rl.MANIFEST).unlink()
    made["no_manifest"] = no_manifest
    made["no_events"] = make_run(root, "NoEventsApp", "COMPRESSED_TIME", 0,
                                 "completed", "failed", counts(4, 0, abandoned=4),
                                 write_events=False)

    return root
