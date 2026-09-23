# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Following a recording that is still being written (viewer.md section 5).

Each hazard the reader contract names gets a test: a record split across two reads, truncation,
rotation, malformed lines, and a file that does not exist yet.
"""

import json
import os

import pytest

from inventzia.pulse.viewers import live_tail as lt
from inventzia.pulse.viewers.contract import event_record as er

from conftest import WINDOW

RID = "live-1"


def ev(seq, **kw):
    return json.dumps(er.event(RID, seq, WINDOW[0] + seq, "ext.bars", "K", "T",
                               kw or {"i": seq})) + "\n"


@pytest.fixture
def live_file(tmp_path):
    return tmp_path / "events.jsonl"


def append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def test_missing_file_is_a_normal_state(live_file):
    """Following can begin before the run has opened its recording."""
    f = lt.RecordingFollower(live_file)
    assert f.poll() == []
    assert f.stats.missing


def test_reads_appended_events_and_then_nothing(live_file):
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    append(live_file, ev(0) + ev(1))
    assert len(f.poll()) == 2
    assert f.stats.header is not None
    assert f.poll() == [], "an idle poll must return nothing, not repeat"
    assert not f.stats.complete


def test_a_record_split_across_polls_is_withheld_until_complete(live_file):
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    f.poll()
    line = ev(7)
    append(live_file, line[:len(line) // 2])
    assert f.poll() == [], "half a record is not a record"
    append(live_file, line[len(line) // 2:])
    got = f.poll()
    assert [r["seq"] for r in got] == [7]


def test_malformed_lines_are_counted_and_skipped(live_file):
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    f.poll()
    append(live_file, "not json\n")
    append(live_file, '{"kind":"event", BROKEN\n')
    append(live_file, '"a bare string"\n')
    append(live_file, '{"kind":"weird","v":1}\n')      # valid JSON, unknown kind
    append(live_file, ev(3))
    got = f.poll()
    assert [r["seq"] for r in got] == [3], "a good record after garbage must still arrive"
    assert f.stats.skipped == 4


def test_truncation_restarts_the_file(live_file):
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    append(live_file, ev(0) + ev(1) + ev(2))
    f.poll()
    with open(live_file, "w", encoding="utf-8") as fh:         # shorter than before
        fh.write(json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
        fh.write(ev(0, i=99))
    got = f.poll()
    assert [r["payload"]["i"] for r in got] == [99]
    assert f.stats.reopened == 1


@pytest.mark.skipif(not hasattr(os.stat("."), "st_ino") or os.stat(".").st_ino == 0,
                    reason="platform does not report inodes")
def test_rotation_restarts_the_file(live_file, tmp_path):
    """Same path, different file underneath — detected by identity, not just size."""
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    append(live_file, ev(0))
    f.poll()

    other = tmp_path / "rotated.jsonl"
    with open(other, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
        fh.write(ev(0) + ev(1) + ev(2))
    os.replace(other, live_file)                                # new inode, same path
    assert len(f.poll()) == 3
    assert f.stats.reopened == 1


def test_trailer_marks_the_recording_complete(live_file):
    f = lt.RecordingFollower(live_file)
    append(live_file, json.dumps(er.header(RID, "REAL_TIME", *WINDOW)) + "\n")
    append(live_file, ev(0))
    f.poll()
    append(live_file, json.dumps(er.trailer(RID, 1, 1, 0, 0, 0, status="complete")) + "\n")
    f.poll()
    assert f.stats.complete
    assert f.stats.trailer["status"] == "complete"
    assert "complete" in f.stats.summary()


def test_follow_once_tolerates_a_recording_cut_off_mid_record(tmp_path):
    """What a crashed recorder leaves: no trailer, and a partial final line."""
    path = tmp_path / "crashed.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(er.header(RID, "COMPRESSED_TIME", *WINDOW)) + "\n")
        fh.write(ev(0))
        fh.write('{"kind":"event","seq":1,')                    # died here
    events, stats = lt.follow_once(path)
    assert len(events) == 1
    assert not stats.complete
    assert stats.skipped == 0, "an incomplete tail is not a malformed record"
