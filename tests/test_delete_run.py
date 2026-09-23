# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Deleting a run.

This is a recursive delete driven by a path out of a dict, so the refusals matter more than the
happy path: everything that is not exactly a `tier/app/runId` directory inside the output root must
be rejected.
"""

from pathlib import Path

import pytest

from inventzia.pulse.viewers import run_browser as rb


def test_deletes_only_the_selected_run(runs_root):
    before = rb.load_runs(runs_root)
    victim = before[0]
    victim_dir = Path(victim["dir"])
    app_dir, tier_dir = victim_dir.parent, victim_dir.parent.parent

    rb.delete_run(victim, runs_root)

    assert not victim_dir.exists()
    assert len(rb.load_runs(runs_root)) == len(before) - 1
    assert app_dir.is_dir() and tier_dir.is_dir(), "only the run goes, not its containers"


def test_refuses_a_path_outside_the_output_root(runs_root, tmp_path):
    outside = tmp_path / "not_a_run"
    outside.mkdir()
    (outside / "keep.txt").write_text("do not delete me")

    with pytest.raises(ValueError, match="outside the output root"):
        rb.delete_run({"dir": str(outside)}, runs_root)
    assert (outside / "keep.txt").exists()


@pytest.mark.parametrize("depth", ["app", "tier", "root"])
def test_refuses_anything_shallower_than_a_run(runs_root, depth):
    run_dir = Path(rb.load_runs(runs_root)[0]["dir"])
    target = {"app": run_dir.parent, "tier": run_dir.parent.parent, "root": runs_root}[depth]

    with pytest.raises((ValueError, FileNotFoundError)):
        rb.delete_run({"dir": str(target)}, runs_root)
    assert Path(target).is_dir()


def test_browser_reflects_a_deletion(qapp, runs_root):
    dlg = rb.make_browser(runs_root)
    before = dlg.table.rowCount()
    dlg.table.selectRow(0)
    assert dlg.btn_delete.isEnabled()

    rb.delete_run(dlg.current_run(), runs_root)
    dlg.reload()

    assert dlg.table.rowCount() == before - 1
    assert not dlg.btn_delete.isEnabled(), "nothing is selected after a reload"
