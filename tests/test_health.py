# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""How a run's health is read out of its manifest.

This is the viewer's central honesty obligation: engine outcome and recording outcome are separate
facts, and a partial capture must never be shown as complete.
"""

import pytest

from inventzia.pulse.viewers import run_browser as rb


def by_app(root, app):
    for run in rb.load_runs(root):
        if run.get("app") == app:
            return run
    raise AssertionError(f"no run for {app}")


@pytest.mark.parametrize("app, expect", [
    ("CleanApp", rb.OK),
    ("CrashedApp", rb.BROKEN),            # the engine failed
    ("BadRecorderApp", rb.PARTIAL),       # the run was fine; its recording was not
    ("LossyApp", rb.PARTIAL),             # completed, but 492 of 500 events never reached disk
    ("InconsistentApp", rb.BROKEN),       # counts contradict themselves
    ("StillRunningApp", rb.RUNNING),      # never finalized
    ("NoManifestApp", rb.UNKNOWN),        # found by its events file alone
    ("NoEventsApp", rb.PARTIAL),          # manifest present, recording absent
])
def test_verdicts(runs_root, app, expect):
    verdict, why = rb.health(by_app(runs_root, app))
    assert verdict == expect, f"{app}: {verdict} ({why})"
    assert why, "every verdict must explain itself"


def test_run_and_recording_outcomes_are_independent(runs_root):
    """The case the manifest's two status fields exist for."""
    run = by_app(runs_root, "BadRecorderApp")
    assert run["runStatus"] == "completed"
    assert run["recordingStatus"] == "failed"
    assert rb.health(run)[0] == rb.PARTIAL, "a good run with a bad recording is partial, not broken"


def test_lossy_run_reports_what_was_lost(runs_root):
    verdict, why = rb.health(by_app(runs_root, "LossyApp"))
    assert verdict == rb.PARTIAL
    assert "492" in why and "500" in why, f"loss should be quantified, got: {why}"


def test_counts_balance_detects_contradiction(runs_root):
    assert rb.counts_balance(by_app(runs_root, "CleanApp")["counts"]) is True
    assert rb.counts_balance(by_app(runs_root, "InconsistentApp")["counts"]) is False
    assert rb.counts_balance(None) is None, "no counts is unknown, not false"


def test_lost_events_counts_every_loss_channel():
    assert rb.lost_events({"observed": 10, "events": 4, "overflow": 3,
                           "serializationErrors": 2, "abandoned": 1}) == 6
    assert rb.lost_events(None) == 0


def test_annotations_distinguish_missing_from_empty(runs_root):
    """A run with no events file is not the same as one whose recording is empty."""
    assert by_app(runs_root, "NoEventsApp")["_events_bytes"] < 0
    assert by_app(runs_root, "CleanApp")["_events_bytes"] > 0


def test_tier_comes_from_the_directory_when_the_manifest_is_gone(runs_root):
    assert by_app(runs_root, "NoManifestApp")["_tier"] in ("historical", "live")


def test_empty_root_lists_nothing(empty_root):
    assert rb.load_runs(empty_root) == []
