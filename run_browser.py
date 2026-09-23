# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Run browser: pick a run out of the Pulse output layout.

Shared by every viewer in this repository, because choosing a run is not specific to any one of
them. It lists what `run_layout.list_runs()` finds under the output root and reports each run's
health from its manifest, keeping the engine outcome (`runStatus`) and the recording outcome
(`recordingStatus`) visibly separate — either can fail while the other succeeds.

A damaged run is exactly the run someone opens a viewer to look at, so nothing here assumes a run
went well: a missing manifest, a missing or empty `events.jsonl`, an unfinished run still marked
`running`, and counts that do not balance are all rendered as themselves rather than hidden.

    python run_browser.py [--root PATH]     # list runs on the console, no Qt needed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "reference"))
import run_layout as rl  # noqa: E402

# Health verdicts, worst first. A viewer uses these to colour a run and to warn about a recording
# it should not present as complete.
OK, PARTIAL, BROKEN, RUNNING, UNKNOWN = "ok", "partial", "broken", "running", "unknown"

# Verdict colours come in pairs, one per theme. Setting a background without also setting the
# foreground is what makes a themed table unreadable: the widget keeps the theme's text colour, so
# light-on-light (or dark-on-dark) is only a theme switch away. Every background set from here is
# therefore paired with the text colour that belongs on it.
_VERDICT_RGB_LIGHT = {
    OK:      (0xE8, 0xF5, 0xE9),
    PARTIAL: (0xFF, 0xF3, 0xE0),
    BROKEN:  (0xFF, 0xEB, 0xEE),
    RUNNING: (0xE3, 0xF2, 0xFD),
    UNKNOWN: (0xEE, 0xEE, 0xEE),
}
_VERDICT_RGB_DARK = {
    OK:      (0x1B, 0x3A, 0x24),
    PARTIAL: (0x4A, 0x37, 0x12),
    BROKEN:  (0x4D, 0x1F, 0x26),
    RUNNING: (0x16, 0x33, 0x4D),
    UNKNOWN: (0x3A, 0x3A, 0x3A),
}
_TEXT_ON_LIGHT = (0x1A, 0x1A, 0x1A)
_TEXT_ON_DARK = (0xEC, 0xEC, 0xEC)

# Tier is a property of the run, not a judgement about it, so it gets its own colour on its own
# column, separate from the verdict's.
_TIER_RGB_LIGHT = {"historical": (0xE4, 0xE4, 0xE4), "live": (0xD6, 0xE9, 0xF8)}
_TIER_RGB_DARK = {"historical": (0x3C, 0x3C, 0x3C), "live": (0x1C, 0x39, 0x52)}


def tier_colors(tier: str, dark: bool | None = None) -> tuple[tuple, tuple]:
    """(background, foreground) RGB triples for a tier: grey for historical, blue for live."""
    if dark is None:
        dark = palette_is_dark()
    table = _TIER_RGB_DARK if dark else _TIER_RGB_LIGHT
    text = _TEXT_ON_DARK if dark else _TEXT_ON_LIGHT
    fallback = (0x3A, 0x3A, 0x3A) if dark else (0xEE, 0xEE, 0xEE)
    return table.get(tier, fallback), text


def palette_is_dark() -> bool:
    """Is the running application using a dark theme? False when there is no Qt app yet."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return False
        return app.palette().window().color().lightness() < 128
    except Exception:
        return False


def verdict_colors(verdict: str, dark: bool | None = None) -> tuple[tuple, tuple]:
    """(background, foreground) RGB triples for a verdict, matched to the active theme."""
    if dark is None:
        dark = palette_is_dark()
    table = _VERDICT_RGB_DARK if dark else _VERDICT_RGB_LIGHT
    text = _TEXT_ON_DARK if dark else _TEXT_ON_LIGHT
    return table.get(verdict, table[UNKNOWN]), text


def counts_balance(counts: dict | None) -> bool | None:
    """Does `observed == events + overflow + serializationErrors + abandoned`?

    None when there are no counts to check (an unfinished run, or a manifest that predates them).
    This identity is the recorder's own accounting; a run where it fails is reporting something
    inconsistent about itself and should never be shown as a complete capture.
    """
    if not counts:
        return None
    try:
        return int(counts["observed"]) == (
            int(counts.get("events", 0)) + int(counts.get("overflow", 0))
            + int(counts.get("serializationErrors", 0)) + int(counts.get("abandoned", 0))
        )
    except (KeyError, TypeError, ValueError):
        return False


def lost_events(counts: dict | None) -> int:
    """How many observed events never reached the recording (overflow + serErr + abandoned)."""
    if not counts:
        return 0
    try:
        return (int(counts.get("overflow", 0)) + int(counts.get("serializationErrors", 0))
                + int(counts.get("abandoned", 0)))
    except (TypeError, ValueError):
        return 0


def health(run: dict) -> tuple[str, str]:
    """Classify a run from its manifest. Returns (verdict, one-line explanation).

    The two statuses are read independently on purpose: "the run failed" and "the recording is
    incomplete" are different facts, and a viewer that collapses them misleads.
    """
    run_status = run.get("runStatus")
    rec_status = run.get("recordingStatus")
    counts = run.get("counts")

    if not run.get("_has_manifest", True):
        return UNKNOWN, "no run.json; found by its events file alone"
    if run_status == "running":
        return RUNNING, "still running (or ended without finalizing its manifest)"

    balanced = counts_balance(counts)
    lost = lost_events(counts)

    if run_status == "failed":
        return BROKEN, "the run failed" + (f"; {lost} event(s) not recorded" if lost else "")
    if run_status == "aborted":
        return BROKEN, "the run was interrupted" + (f"; {lost} event(s) not recorded" if lost else "")
    if rec_status in ("failed", "interrupted"):
        return PARTIAL, f"the run completed but its recording {rec_status}"
    if balanced is False:
        return BROKEN, "the recording's counts do not balance; treat it as unreliable"
    if lost:
        return PARTIAL, f"{lost} of {counts.get('observed')} observed event(s) were not recorded"
    if run_status == "completed" and rec_status == "complete":
        return OK, "completed with a complete recording"
    return UNKNOWN, f"runStatus={run_status!r}, recordingStatus={rec_status!r}"


def describe(run: dict) -> str:
    """A compact one-line summary of a run's counts, for a status bar."""
    counts = run.get("counts") or {}
    if not counts:
        return "no counts recorded"
    parts = [f"{counts.get('events', 0)} recorded", f"{counts.get('observed', 0)} observed"]
    for field, label in (("overflow", "dropped"), ("serializationErrors", "serialization errors"),
                         ("abandoned", "abandoned")):
        n = counts.get(field, 0)
        if n:
            parts.append(f"{n} {label}")
    return ", ".join(parts)


def delete_run(run: dict, root: str | Path | None = None) -> None:
    """Delete one run directory and everything in it. Irreversible.

    Refuses anything that is not a run directory strictly inside the output root, so a malformed
    entry can never turn into a recursive delete somewhere else. The caller is responsible for
    asking the user first.
    """
    import shutil

    base = Path(resolve_root(root)).resolve()
    target = Path(run["dir"]).resolve()
    if not rl._is_within(base, target):
        raise ValueError(f"refusing to delete outside the output root: {target}")
    if target == base or len(target.relative_to(base).parts) != 3:
        # tier/app/runId, exactly: never a tier or an application directory.
        raise ValueError(f"not a run directory: {target}")
    if not target.is_dir():
        raise FileNotFoundError(target)
    shutil.rmtree(target)


def load_runs(root: str | Path | None = None) -> list[dict]:
    """Every run under the root, newest first, annotated for display.

    Adds `_has_manifest`, `_tier`, `_events_path` and `_events_bytes` so the browser can tell a run
    with no recording from one whose recording is merely empty, without re-reading the tree.
    """
    runs = rl.list_runs(root)
    for run in runs:
        directory = Path(run["dir"])
        run["_has_manifest"] = (directory / rl.MANIFEST).is_file()
        # Prefer the manifest's tier; fall back to the directory, which is authoritative for shape.
        run["_tier"] = run.get("tier") or _tier_from_dir(directory)
        events = directory / rl.EVENTS
        run["_events_path"] = events
        run["_events_bytes"] = events.stat().st_size if events.is_file() else -1
    return runs


def _tier_from_dir(directory: Path) -> str:
    try:
        return directory.parent.parent.name
    except (AttributeError, IndexError):
        return "?"


def _fmt_started(run: dict) -> str:
    created = run.get("createdAt") or ""
    return created.replace("T", " ")[:19] if isinstance(created, str) else ""


# ----------------------------------------------------------------------------------------------
# Qt widget. Imported lazily so the console listing above works without PySide6 installed.
# ----------------------------------------------------------------------------------------------

def _qt():
    from PySide6.QtCore import Qt, QSettings  # noqa: F401
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
                                   QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                                   QTableWidget, QTableWidgetItem, QVBoxLayout)
    return (Qt, QSettings, QColor, QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
            QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
            QTableWidgetItem, QVBoxLayout)


# The product name, shown on every window. Defined here because it belongs to the application as a
# whole rather than to any one viewer.
APP_NAME = "Inventzia's Pulse Run Viewer"

# Where a chosen output root is remembered between sessions, so the root is picked once rather
# than supplied on every launch. These two keep their existing values: changing them would orphan
# an already-remembered output folder.
_ORG, _APP = "Inventzia", "pulse-viewers"
_ROOT_KEY = "runBrowser/outputRoot"


def remembered_root():
    """The last root chosen in the UI, if it still exists."""
    from PySide6.QtCore import QSettings
    value = QSettings(_ORG, _APP).value(_ROOT_KEY)
    if value:
        p = Path(value)
        if p.is_dir():
            return p
    return None


def resolve_root(explicit=None):
    """Explicit argument, else the remembered choice, else $PULSE_OUTPUT, else the default.

    The remembered choice sits above the environment on purpose: someone who picked a root in the
    UI meant it, and should not have to set a variable to make it stick.
    """
    if explicit:
        return Path(explicit)
    try:
        chosen = remembered_root()
    except Exception:                      # no Qt, or unreadable settings: fall through
        chosen = None
    return chosen or rl.output_root(None)


COLUMNS = ["tier", "app", "run", "started", "run status", "recording", "events", "health"]
COL_TIER, COL_HEALTH = 0, len(COLUMNS) - 1     # the only two columns that carry a colour


def make_browser(root=None, parent=None, on_open=None):
    """Build the run browser as a persistent, modeless window.

    It is the application's hub rather than a one-shot picker: opening a run calls `on_open(run)`
    and leaves the browser standing, so several runs can be opened side by side. `chosen` holds the
    most recently opened run.
    """
    (Qt, QSettings, QColor, QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
     QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
     QVBoxLayout) = _qt()

    class RunBrowserWindow(QDialog):
        def __init__(self) -> None:
            super().__init__(parent)
            self.setWindowTitle(f"{APP_NAME} - runs")
            self.resize(1000, 460)
            # A modeless window, not a dialog blocking its parent: viewers opened from here stay
            # usable while the browser remains available to open more.
            self.setModal(False)
            self.setWindowFlag(Qt.Window, True)
            self.chosen: dict | None = None
            self._on_open = on_open
            self._root = resolve_root(root)
            self._runs: list[dict] = []

            self.header = QLabel()
            self.header.setWordWrap(True)

            self.table = QTableWidget(0, len(COLUMNS))
            self.table.setHorizontalHeaderLabels(COLUMNS)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.table.doubleClicked.connect(self.open_current)
            self.table.itemSelectionChanged.connect(self._on_select)

            self.detail = QLabel("select a run")
            self.detail.setWordWrap(True)

            change_root = QPushButton("Change output folder...")
            change_root.setToolTip("Pick the Pulse output root to browse ($PULSE_OUTPUT)")
            change_root.clicked.connect(self._change_root)
            open_file = QPushButton("Open recording file...")
            open_file.setToolTip("Open a single events.jsonl directly, outside the output layout")
            open_file.clicked.connect(self._open_loose_file)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self.reload)
            self.btn_delete = QPushButton("Delete run...")
            self.btn_delete.setToolTip("Permanently delete the selected run directory")
            self.btn_delete.setEnabled(False)
            self.btn_delete.clicked.connect(self.delete_current)

            # "Close" rather than "Cancel": closing the browser dismisses this window only, and
            # leaves any viewer windows already opened from it alone.
            self.buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Close)
            self.buttons.accepted.connect(self.open_current)
            self.buttons.rejected.connect(self.close)
            self.buttons.button(QDialogButtonBox.Open).setEnabled(False)
            self.buttons.button(QDialogButtonBox.Open).setToolTip(
                "Open this run in its own window; the browser stays open")

            bar = QHBoxLayout()
            bar.addWidget(change_root)
            bar.addWidget(open_file)
            bar.addWidget(refresh)
            bar.addWidget(self.btn_delete)
            bar.addStretch(1)
            bar.addWidget(self.buttons)

            layout = QVBoxLayout(self)
            layout.addWidget(self.header)
            layout.addWidget(self.table, 1)
            layout.addWidget(self.detail)
            layout.addLayout(bar)

            self.reload()

        # ------------------------------------------------------------------
        def reload(self) -> None:
            self._runs = load_runs(self._root)
            self.table.setRowCount(len(self._runs))
            for row, run in enumerate(self._runs):
                verdict, _why = health(run)
                counts = run.get("counts") or {}
                events = counts.get("events")
                if events is None:
                    events = "-" if run["_events_bytes"] < 0 else ""
                cells = [
                    run.get("_tier", "?"),
                    run.get("app", "?"),
                    run.get("runId", "?"),
                    _fmt_started(run),
                    str(run.get("runStatus") or "-"),
                    str(run.get("recordingStatus") or "-"),
                    str(events),
                    verdict,
                ]
                # Only the two columns that *mean* a colour carry one: the tier and the verdict.
                # Colouring whole rows drowns both signals -- a list of healthy runs becomes a wall
                # of green -- and fights the theme's own alternating-row shading.
                painted = {
                    COL_TIER: tier_colors(run.get("_tier", "?")),
                    COL_HEALTH: verdict_colors(verdict),
                }
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col in painted:
                        bg, fg = painted[col]
                        item.setBackground(QColor(*bg))
                        item.setForeground(QColor(*fg))   # never leave text to the theme
                    self.table.setItem(row, col, item)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            n = len(self._runs)
            self.header.setText(f"<b>{self._root}</b> &mdash; {n} run{'s' if n != 1 else ''}")
            self.buttons.button(QDialogButtonBox.Open).setEnabled(False)
            self.btn_delete.setEnabled(False)
            if n:
                self.detail.setText("select a run")
            else:
                # An empty list is the one state that needs to explain itself: say what was looked
                # at, and what to do about it, rather than showing a disabled button.
                exists = self._root.is_dir()
                self.detail.setText(
                    ("No runs under this folder." if exists else "This folder does not exist.")
                    + " Pulse writes runs as"
                    " <code>&lt;root&gt;/{historical|live}/&lt;app&gt;/&lt;runId&gt;/</code>."
                    "<br>Use <b>Change output folder...</b> to point at your Pulse output root"
                    " (often <code>PulseOut</code>), or <b>Open recording file...</b> for a single"
                    " <code>events.jsonl</code>.")

        def _change_root(self) -> None:
            picked = QFileDialog.getExistingDirectory(
                self, "Choose the Pulse output folder", str(self._root))
            if picked:
                self._root = Path(picked)
                QSettings(_ORG, _APP).setValue(_ROOT_KEY, str(self._root))   # remember it
                self.reload()

        def _open_loose_file(self) -> None:
            picked, _ = QFileDialog.getOpenFileName(
                self, "Open a recording", str(self._root),
                "Event recordings (*.jsonl);;All files (*)")
            if picked:
                # Not part of any run directory, so it has no manifest: flagged so the viewer reads
                # its health from the recording's own trailer instead.
                self.chosen = {"_events_path": Path(picked), "_loose": True}
                if self._on_open is not None:
                    self._on_open(self.chosen)

        def current_run(self) -> dict | None:
            rows = {i.row() for i in self.table.selectedIndexes()}
            return self._runs[rows.pop()] if len(rows) == 1 else None

        def _on_select(self) -> None:
            run = self.current_run()
            if run is None:
                return
            verdict, why = health(run)
            openable = run["_events_bytes"] > 0
            self.detail.setText(f"<b>{verdict}</b> &mdash; {why}<br>{describe(run)}"
                                + ("" if openable else "<br><i>no events to open</i>"))
            self.buttons.button(QDialogButtonBox.Open).setEnabled(openable)
            self.btn_delete.setEnabled(True)      # any listed run can be deleted, damaged included

        def delete_current(self) -> None:
            """Delete the selected run, after saying plainly what is about to be destroyed."""
            run = self.current_run()
            if run is None:
                return
            running = run.get("runStatus") == "running"
            note = ("<br><br><b>This run appears to be still running.</b> Deleting it now will "
                    "pull the files out from under the engine that is writing them.") if running else ""
            answer = QMessageBox.warning(
                self, "Delete run",
                f"Permanently delete this run and everything in it?<br><br>"
                f"<b>{run.get('app', '?')} / {run.get('runId', '?')}</b><br>"
                f"<code>{run.get('dir', '?')}</code>{note}<br><br>This cannot be undone.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            try:
                delete_run(run, self._root)
            except Exception as exc:
                QMessageBox.critical(self, "Delete failed", f"Could not delete the run:\n{exc}")
                return
            self.reload()

        def open_current(self) -> None:
            """Hand the selected run to the application. The browser deliberately stays open."""
            run = self.current_run()
            if run is not None and run["_events_bytes"] > 0:
                self.chosen = run
                if self._on_open is not None:
                    self._on_open(run)

    return RunBrowserWindow()


def main() -> int:
    """Console listing, so the layout can be inspected without a GUI."""
    root = None
    argv = sys.argv[1:]
    if argv and argv[0] == "--root" and len(argv) > 1:
        root = argv[1]
    runs = load_runs(root)
    print(f"output root: {rl.output_root(root)}")
    if not runs:
        print("  (no runs)")
        return 0
    for run in runs:
        verdict, why = health(run)
        print(f"  {run.get('_tier','?'):10} {run.get('app','?'):28} {run.get('runId','?')}")
        print(f"    {verdict:8} {why}")
        print(f"    {describe(run)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
