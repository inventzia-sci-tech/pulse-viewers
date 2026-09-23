# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Pulse Events Viewer, phase 2 (offline).

One of the viewers hosted in pulse-viewers; this one reads the event recording a Pulse run writes.
Reads a completed recording (JSONL, see schema/event-record.schema.json), validates it against the
contract, and shows the events in a sortable, filterable grid with per-type colour and a payload
detail tree. Qt only; no engine, no JVM, no domain adapters. Live following is phase 3.

With no argument it opens the run browser over the Pulse output root, so a run is chosen by what it
is rather than by remembering a path. A run's health travels with it into the header bar: the engine
outcome and the recording outcome stay separate, and a partial capture is labelled as partial.

    python event_viewer.py                    # browse $PULSE_OUTPUT
    python event_viewer.py --root PATH        # browse a specific output root
    python event_viewer.py recording.jsonl    # open one recording directly
"""

from __future__ import annotations

import colorsys
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / "reference"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_record as er  # noqa: E402
import live_tail as lt  # noqa: E402
import run_browser as rb  # noqa: E402

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,  # noqa: E402
                            QTimer)
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox,
    QSplitter,
    QTableView, QTreeView, QVBoxLayout, QWidget,
)

POLL_MS = 400          # how often a followed recording is checked for new bytes

COLUMNS = ["seq", "observed", "event time", "key", "topic", "type", "payload"]
COL_SEQ, COL_OBSERVED, COL_EVENTTIME, COL_KEY, COL_TOPIC, COL_TYPE, COL_PAYLOAD = range(7)
RAW_ROLE = Qt.UserRole + 1
DEFAULT_MAX_ROWS = 500_000


def type_color(type_id: str) -> QColor:
    """Stable per-typeId background (same type, same hue across sessions), matched to the theme.

    Lightness follows the theme rather than being fixed: a pale pastel under a dark theme leaves the
    theme's light text on a light cell, which is unreadable. Pair every call with `type_text_color`.
    """
    h = (hash(type_id) % 360) / 360.0
    lightness = 0.26 if rb.palette_is_dark() else 0.88
    r, g, b = colorsys.hls_to_rgb(h, lightness, 0.55)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def type_text_color() -> QColor:
    """The text colour that stays readable on `type_color`'s background."""
    return QColor(0xEC, 0xEC, 0xEC) if rb.palette_is_dark() else QColor(0x1A, 0x1A, 0x1A)


def _short_type(type_id: str) -> str:
    return type_id.rsplit(".", 1)[-1] if "." in type_id else type_id


def _fmt_epoch_ms(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.") \
            + f"{ms % 1000:03d}"
    except (OverflowError, OSError, ValueError):
        return str(ms)


def _fmt_observed(iso: str) -> str:
    return iso.replace("T", " ")[:23] if isinstance(iso, str) else str(iso)


class EventTableModel(QAbstractTableModel):
    """Holds event records (bounded ring buffer). Stores the raw record per row; the payload is
    parsed into a tree only on selection (lazy). Inserts happen in batches."""

    def __init__(self, max_rows: int = DEFAULT_MAX_ROWS) -> None:
        super().__init__()
        self._rows: list[dict] = []
        self._max_rows = max_rows

    def clear(self) -> None:
        """Drop every row (opening another run reuses the window)."""
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def append_events(self, events: list[dict]) -> None:
        if not events:
            return
        # bounded retention: keep the most recent max_rows
        overflow = len(self._rows) + len(events) - self._max_rows
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self._rows[:overflow]
            self.endRemoveRows()
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(events) - 1)  # one batch insert
        self._rows.extend(events)
        self.endInsertRows()

    # Qt model interface -------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rec = self._rows[index.row()]
        col = index.column()
        if role == RAW_ROLE:
            return rec
        if role == Qt.BackgroundRole and col == COL_TYPE:
            return type_color(rec.get("typeId", ""))
        if role == Qt.ForegroundRole and col == COL_TYPE:
            return type_text_color()      # set with the background, or the theme wins and clashes
        if role == Qt.ToolTipRole and col == COL_TYPE:
            return rec.get("typeId", "")
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == COL_SEQ:
                return rec.get("seq")
            if col == COL_OBSERVED:
                return _fmt_observed(rec.get("observedAt", ""))
            if col == COL_EVENTTIME:
                return _fmt_epoch_ms(rec.get("eventTime", 0))
            if col == COL_KEY:
                return rec.get("key", "")
            if col == COL_TOPIC:
                return rec.get("topic", "")
            if col == COL_TYPE:
                return _short_type(rec.get("typeId", ""))
            if col == COL_PAYLOAD:
                return json.dumps(rec.get("payload", {}), separators=(",", ":"))[:200]
        return None


class EventFilterProxy(QSortFilterProxyModel):
    """Filters by topic/key/type/text and a seq range; sorts by the active column with a seq
    tiebreak, so e.g. sorting by 'type' groups by type and orders by seq within (two criteria)."""

    def __init__(self) -> None:
        super().__init__()
        self.f_topic = self.f_key = self.f_type = ""   # "" = any
        self.f_text = ""
        self.seq_min: int | None = None
        self.seq_max: int | None = None

    def _rec(self, source_row: int) -> dict:
        return self.sourceModel().data(self.sourceModel().index(source_row, 0), RAW_ROLE)

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        rec = self._rec(source_row)
        if self.f_topic and rec.get("topic") != self.f_topic:
            return False
        if self.f_key and rec.get("key") != self.f_key:
            return False
        if self.f_type and rec.get("typeId") != self.f_type:
            return False
        seq = rec.get("seq", 0)
        if self.seq_min is not None and seq < self.seq_min:
            return False
        if self.seq_max is not None and seq > self.seq_max:
            return False
        if self.f_text:
            hay = f"{rec.get('key','')} {rec.get('topic','')} {rec.get('typeId','')} " \
                  f"{json.dumps(rec.get('payload', {}))}".lower()
            if self.f_text.lower() not in hay:
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a = self.sourceModel().data(left, RAW_ROLE)
        b = self.sourceModel().data(right, RAW_ROLE)
        col = left.column()
        keymap = {COL_SEQ: "seq", COL_OBSERVED: "observedAt", COL_EVENTTIME: "eventTime",
                  COL_KEY: "key", COL_TOPIC: "topic", COL_TYPE: "typeId"}
        field = keymap.get(col)
        if field and field != "seq":
            av, bv = a.get(field), b.get(field)
            if av != bv:
                return av < bv
        return a.get("seq", 0) < b.get("seq", 0)   # tiebreak / primary: dispatch order


def build_payload_tree(record: dict) -> QStandardItemModel:
    """Build a detail tree for one event (lazy: only called on selection)."""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["field", "value"])

    def add(parent: QStandardItem, key, value) -> None:
        if isinstance(value, dict):
            node = QStandardItem(str(key))
            parent.appendRow([node, QStandardItem("{...}")])
            for k, v in value.items():
                add(node, k, v)
        elif isinstance(value, list):
            node = QStandardItem(str(key))
            parent.appendRow([node, QStandardItem(f"[{len(value)}]")])
            for i, v in enumerate(value):
                add(node, i, v)
        else:
            parent.appendRow([QStandardItem(str(key)), QStandardItem(json.dumps(value))])

    root = model.invisibleRootItem()
    for k in ("seq", "observedAt", "eventTime", "topic", "key", "typeId"):
        root.appendRow([QStandardItem(k), QStandardItem(json.dumps(record.get(k)))])
    add(root, "payload", record.get("payload", {}))
    return model


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None, run: dict | None = None,
                 root: str | None = None, controller: "ViewerApp | None" = None,
                 follow: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(rb.APP_NAME)
        self._root = root
        self._controller = controller
        self._path: str | None = None
        self._run: dict | None = None
        self._follower: lt.RecordingFollower | None = None
        self._known: dict[str, set] = {"topic": set(), "key": set(), "typeId": set()}
        self._seq_auto = True
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll_live)

        self.model = EventTableModel()
        self.proxy = EventFilterProxy()
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.selectionModel().selectionChanged.connect(self._on_select)

        self.detail = QTreeView()
        self.detail.setHeaderHidden(False)

        self.meta = QLabel("no recording loaded")
        self.meta.setWordWrap(True)

        # Run health, kept on its own line and colour-coded: it is the first thing to read about a
        # recording, and must never be lost among the metadata.
        self.health = QLabel()
        self.health.setWordWrap(True)
        self.health.setVisible(False)

        # filter controls
        self.cb_topic = QComboBox(); self.cb_key = QComboBox(); self.cb_type = QComboBox()
        for cb in (self.cb_topic, self.cb_key, self.cb_type):
            cb.currentIndexChanged.connect(self._apply_filters)
        self.txt = QLineEdit(); self.txt.setPlaceholderText("search key/topic/type/payload")
        self.txt.textChanged.connect(self._apply_filters)
        self.seq_min = QSpinBox(); self.seq_max = QSpinBox()
        for sp in (self.seq_min, self.seq_max):
            sp.setRange(0, 2_000_000_000); sp.valueChanged.connect(self._apply_filters)
        # Only a user edit reaches this: programmatic updates block the signal.
        self.seq_max.valueChanged.connect(self._on_seq_max_edited)

        self.btn_open = QPushButton("Run browser...")
        self.btn_open.setToolTip("Show the run browser; runs open in their own windows")
        self.btn_open.clicked.connect(self.browse_runs)

        self.btn_follow = QPushButton("Follow")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setEnabled(False)
        self.btn_follow.setToolTip("Watch this recording for new events as the run writes them")
        self.btn_follow.toggled.connect(self.set_following)

        self.follow_state = QLabel()
        self.follow_state.setVisible(False)

        controls = QHBoxLayout()
        controls.addWidget(self.btn_open)
        controls.addWidget(self.btn_follow)
        for lbl, w in (("topic", self.cb_topic), ("key", self.cb_key), ("type", self.cb_type),
                       ("seq >=", self.seq_min), ("seq <=", self.seq_max)):
            controls.addWidget(QLabel(lbl)); controls.addWidget(w)
        controls.addWidget(self.txt, 1)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([600, 220])

        central = QWidget(); layout = QVBoxLayout(central)
        layout.addWidget(self.meta)
        layout.addWidget(self.health)
        layout.addWidget(self.follow_state)
        layout.addLayout(controls)
        layout.addWidget(split, 1)
        self.setCentralWidget(central)
        self.resize(1100, 760)

        # The window exists before any recording does, so browsing never leaves the application
        # without a window and picking a run replaces the contents rather than the window.
        if path is not None:
            self.load(path, run, follow=follow)
        else:
            self.meta.setText("No recording loaded &mdash; use <b>Open run...</b>")

    # ----------------------------------------------------------------
    def browse_runs(self) -> None:
        """Bring the run browser forward. It is a separate, persistent window, and runs opened from
        it get their own viewer window, so this never disturbs what is already on screen."""
        if self._controller is not None:
            self._controller.show_browser()

    def load(self, path: str, run: dict | None = None, follow: bool = False) -> None:
        """Open a recording. `follow` reads it through the tailer and keeps watching for more.

        The two paths are deliberately distinct. A finished recording is validated against the
        schema, which reports every defect. One still being written has no trailer yet and would
        fail that validation for no reason, so it is read through the follower instead -- and read
        *entirely* through it, so the initial contents and the live tail come from one position in
        one file, with no gap or overlap between them.
        """
        self.set_following(False)
        self.model.clear()
        self._path = path
        self._run = run
        if follow:
            self._follower = lt.RecordingFollower(path)
            events = self._follower.poll()
            report = self._report_from_follow(self._follower.stats, path)
        else:
            self._follower = None
            report = er.validate(path)
            events = list(er.read(path))
        self.model.append_events(events)
        self.proxy.sort(COL_SEQ, Qt.DescendingOrder)   # newest first
        self._populate_filters(events)
        self._show_meta(path, report, len(events))
        self._show_health(run, report, len(events))
        self.btn_follow.setEnabled(True)
        if follow:
            self.set_following(True)
        # Product name first, then what this particular window holds, so several open windows are
        # still tellable apart in a taskbar that truncates.
        subject = f"{run['app']} / {run['runId']}" if run else Path(path).name
        self.setWindowTitle(f"{rb.APP_NAME} - {subject}")

    # ---- live following ---------------------------------------------
    @staticmethod
    def _report_from_follow(stats: "lt.FollowStats", path: str) -> er.Report:
        """Present the follower's view in the same shape the static reader returns."""
        return er.Report(ok=stats.skipped == 0, diagnostics=[], header=stats.header,
                         trailer=stats.trailer, event_count=stats.events,
                         run_id=(stats.header or {}).get("runId"))

    def set_following(self, on: bool) -> None:
        """Start or stop watching the open recording for new events."""
        if on and self._follower is None and self._path:
            # Following was switched on for a recording opened statically: pick up from the end so
            # the rows already shown are not delivered a second time.
            self._follower = lt.RecordingFollower(self._path)
            try:
                self._follower._pos = Path(self._path).stat().st_size
            except OSError:
                pass
        if on and self._follower is None:
            return
        if self.btn_follow.isChecked() != on:
            self.btn_follow.blockSignals(True)
            self.btn_follow.setChecked(on)
            self.btn_follow.blockSignals(False)
        if on:
            self._timer.start()
        else:
            self._timer.stop()
        self._show_follow_state()

    def _poll_live(self) -> None:
        """One tick of the follow: take whatever has been appended and show it."""
        if self._follower is None:
            return
        try:
            events = self._follower.poll()
        except Exception as exc:            # a follow must never take the window down with it
            print(f"follow error: {exc}", file=sys.stderr)
            self.set_following(False)
            return
        if events:
            self.model.append_events(events)
            self._merge_filters(events)
        if self._follower.stats.complete:
            # The recorder wrote its trailer: there will be no more, so stop and show the final
            # verdict from the completed recording.
            self.set_following(False)
            report = self._report_from_follow(self._follower.stats, self._path)
            self._show_health(self._run, report, self.model.rowCount())
        self._show_follow_state()

    def _show_follow_state(self) -> None:
        stats = self._follower.stats if self._follower else None
        if stats is None:
            self.follow_state.setVisible(False)
            return
        if self._timer.isActive():
            state = "following" if not stats.missing else "waiting for the recording to appear"
        else:
            state = "recording complete" if stats.complete else "paused"
        parts = [state, f"{self.model.rowCount()} rows"]
        if stats.skipped:
            parts.append(f"{stats.skipped} unreadable line(s)")
        if stats.reopened:
            parts.append(f"re-opened {stats.reopened}x")
        self.follow_state.setText(" &mdash; ".join(parts))
        self.follow_state.setVisible(True)

    def _show_health(self, run: dict | None, report: er.Report, n: int) -> None:
        """Surface the run's own verdict. Without a manifest (a bare .jsonl) fall back to the
        recording's trailer, which carries the same accounting."""
        if run is None:
            trailer = report.trailer or {}
            if not trailer:
                self.health.setVisible(False)
                return
            counts = {k: trailer.get(k, 0) for k in
                      ("observed", "events", "overflow", "serializationErrors", "abandoned")}
            run = {"runStatus": "completed", "recordingStatus": trailer.get("status"),
                   "counts": counts, "_has_manifest": True}
        verdict, why = rb.health(run)
        bg, fg = rb.verdict_colors(verdict)
        badge = "background:#%02X%02X%02X; color:#%02X%02X%02X" % (*bg, *fg)
        self.health.setText(
            f"<span style='{badge}'>&nbsp;<b>{verdict.upper()}</b>&nbsp;</span> "
            f"{why} &mdash; {rb.describe(run)}")
        self.health.setVisible(True)
        # The grid shows only what was recorded; say so when that is less than what ran.
        lost = rb.lost_events(run.get("counts"))
        if lost:
            print(f"warning: {lost} observed event(s) are absent from this recording; "
                  f"the grid shows {n} of {(run.get('counts') or {}).get('observed', '?')}",
                  file=sys.stderr)

    def _populate_filters(self, events: list[dict]) -> None:
        """Reset the filters for a freshly loaded recording."""
        for cb in (self.cb_topic, self.cb_key, self.cb_type):
            cb.blockSignals(True); cb.clear(); cb.addItem("(all)", "")
            cb.blockSignals(False)
        self._known = {"topic": set(), "key": set(), "typeId": set()}
        self._seq_auto = True            # the upper bound tracks the data until the user sets one
        self.seq_min.setValue(0)
        self._merge_filters(events)

    def _merge_filters(self, events: list[dict]) -> None:
        """Fold newly arrived events into the filters without disturbing the user's choices.

        Live events bring new topics, keys and types, and push `seq` past whatever upper bound was
        set when the recording was first read. Rebuilding the combo boxes would throw away the
        user's selection mid-follow, and leaving `seq_max` behind would silently filter out exactly
        the new events they are watching for -- so entries are added, and the bound follows the data
        until the user takes it over.
        """
        if not events:
            return
        for cb, field, is_type in ((self.cb_topic, "topic", False),
                                   (self.cb_key, "key", False),
                                   (self.cb_type, "typeId", True)):
            fresh = sorted({e.get(field, "") for e in events} - self._known[field])
            if fresh:
                cb.blockSignals(True)     # adding items never moves currentIndex, so the choice holds
                for v in fresh:
                    cb.addItem(_short_type(v) if is_type else v, v)
                cb.blockSignals(False)
                self._known[field].update(fresh)

        max_seq = max((e.get("seq", 0) for e in events), default=0)
        if self._seq_auto and max_seq >= self.seq_max.value():
            self.seq_max.blockSignals(True)
            self.seq_max.setRange(0, max(max_seq, 1))
            self.seq_max.setValue(max_seq)
            self.seq_max.blockSignals(False)
            self.proxy.seq_max = max_seq          # signals were blocked; tell the proxy directly
            self.proxy.invalidateFilter()

    def _on_seq_max_edited(self) -> None:
        """The user set an upper bound, so stop moving it for them."""
        self._seq_auto = False

    def _apply_filters(self) -> None:
        self.proxy.f_topic = self.cb_topic.currentData() or ""
        self.proxy.f_key = self.cb_key.currentData() or ""
        self.proxy.f_type = self.cb_type.currentData() or ""
        self.proxy.f_text = self.txt.text()
        self.proxy.seq_min = self.seq_min.value()
        self.proxy.seq_max = self.seq_max.value()
        self.proxy.invalidateFilter()

    def _on_select(self) -> None:
        idx = self.table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        rec = self.proxy.data(self.proxy.index(idx.row(), 0), RAW_ROLE)
        self.detail.setModel(build_payload_tree(rec))   # lazy: parse only the selected event
        self.detail.expandToDepth(1)

    def _show_meta(self, path: str, report: er.Report, n: int) -> None:
        h = report.header or {}
        t = report.trailer or {}
        fp = h.get("typeFingerprint")
        parts = [
            f"file: {Path(path).name}",
            f"run: {report.run_id}",
            f"mode: {h.get('operatingMode', '?')}",
            f"window: [{h.get('window', {}).get('start', '?')}..{h.get('window', {}).get('end', '?')}]",
            f"fingerprint: {(fp[:12] + '...') if fp else 'null'}",
            f"events: {n}",
        ]
        if t:
            parts.append(f"trailer: {t.get('status')} (observed {t.get('observed')}, "
                         f"overflow {t.get('overflow', 0)}, serErr {t.get('serializationErrors', 0)}, "
                         f"abandoned {t.get('abandoned', 0)})")
        if not report.ok:
            parts.append(f"VALIDATION: {len(report.diagnostics)} issue(s) (see console)")
            for lineno, msg in report.diagnostics:
                print(f"  line {lineno}: {msg}", file=sys.stderr)
        self.meta.setText("   |   ".join(parts))


class ViewerApp:
    """Owns the run browser and one viewer window per opened run.

    The browser is a standing window rather than a modal step, and each run gets its own detached
    viewer, so several runs can be compared side by side. Windows are held here because Qt does not
    own them: dropping the last Python reference would destroy a window that is still on screen.
    """

    def __init__(self, root: str | None = None) -> None:
        self.root = root
        self.browser = None
        self.windows: list[MainWindow] = []

    def show_browser(self) -> None:
        """Show the browser, creating it on first use, and raise it if already open."""
        if self.browser is None:
            self.browser = rb.make_browser(self.root, on_open=self.open_run)
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()

    def open_run(self, run: dict) -> None:
        """Open one run in a new window. A loose file has no manifest, so it carries no run dict."""
        # A run whose manifest still says 'running' is being written now, so follow it by default:
        # that is the whole point of opening a live run.
        follow = run.get("runStatus") == "running"
        self.open_path(str(run["_events_path"]),
                       None if run.get("_loose") else run, follow=follow)

    def open_path(self, path: str, run: dict | None = None, follow: bool = False) -> MainWindow:
        win = MainWindow(path, run, self.root, self, follow=follow)
        # Cascade slightly so a second window does not land exactly on the first.
        offset = 28 * (len(self.windows) % 8)
        win.move(win.x() + offset, win.y() + offset)
        win.show()
        win.raise_()
        self.windows.append(win)
        win.destroyed.connect(lambda *_: self._forget(win))
        return win

    def _forget(self, win) -> None:
        if win in self.windows:
            self.windows.remove(win)


def main() -> int:
    argv = sys.argv[1:]
    root: str | None = None
    if argv and argv[0] == "--root":
        if len(argv) < 2:
            print("--root needs a path", file=sys.stderr)
            return 2
        root, argv = argv[1], argv[2:]
    path = argv[0] if argv else None

    if path is not None and not Path(path).exists():
        print(f"no such recording: {path}", file=sys.stderr)
        return 2

    app = QApplication(sys.argv)
    app.setApplicationName(rb.APP_NAME)          # taskbar / window-manager identity
    app.setOrganizationName("Inventzia")
    viewer = ViewerApp(root)

    if path is None:
        viewer.show_browser()             # the browser is the hub; runs open beside it
    else:
        viewer.open_path(path)            # a named recording opens straight into its own window
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
