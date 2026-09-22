# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Pulse Events Viewer, phase 2 (offline).

Reads a completed recording (JSONL, see schema/event-record.schema.json), validates it against the
contract, and shows the events in a sortable, filterable grid with per-type colour and a payload
detail tree. Qt only; no engine, no JVM, no domain adapters. Live following is phase 3.

    python viewer.py [recording.jsonl]
"""

from __future__ import annotations

import colorsys
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / "reference"))
import event_record as er  # noqa: E402

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt)  # noqa: E402
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QSpinBox, QSplitter,
    QTableView, QTreeView, QVBoxLayout, QWidget,
)

COLUMNS = ["seq", "observed", "event time", "key", "topic", "type", "payload"]
COL_SEQ, COL_OBSERVED, COL_EVENTTIME, COL_KEY, COL_TOPIC, COL_TYPE, COL_PAYLOAD = range(7)
RAW_ROLE = Qt.UserRole + 1
DEFAULT_MAX_ROWS = 500_000


def type_color(type_id: str) -> QColor:
    """Stable light pastel per typeId (same type, same colour across sessions)."""
    h = (hash(type_id) % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(h, 0.88, 0.55)  # high lightness so black text stays readable
    return QColor(int(r * 255), int(g * 255), int(b * 255))


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
    def __init__(self, path: str) -> None:
        super().__init__()
        self.setWindowTitle("Pulse Events Viewer")

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

        # filter controls
        self.cb_topic = QComboBox(); self.cb_key = QComboBox(); self.cb_type = QComboBox()
        for cb in (self.cb_topic, self.cb_key, self.cb_type):
            cb.currentIndexChanged.connect(self._apply_filters)
        self.txt = QLineEdit(); self.txt.setPlaceholderText("search key/topic/type/payload")
        self.txt.textChanged.connect(self._apply_filters)
        self.seq_min = QSpinBox(); self.seq_max = QSpinBox()
        for sp in (self.seq_min, self.seq_max):
            sp.setRange(0, 2_000_000_000); sp.valueChanged.connect(self._apply_filters)

        controls = QHBoxLayout()
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
        layout.addLayout(controls)
        layout.addWidget(split, 1)
        self.setCentralWidget(central)
        self.resize(1100, 760)

        self.load(path)

    # ----------------------------------------------------------------
    def load(self, path: str) -> None:
        report = er.validate(path)
        events = list(er.read(path))
        self.model.append_events(events)
        self.proxy.sort(COL_SEQ, Qt.DescendingOrder)   # newest first
        self._populate_filters(events)
        self._show_meta(path, report, len(events))

    def _populate_filters(self, events: list[dict]) -> None:
        topics = sorted({e.get("topic", "") for e in events})
        keys = sorted({e.get("key", "") for e in events})
        types = sorted({e.get("typeId", "") for e in events})
        max_seq = max((e.get("seq", 0) for e in events), default=0)
        for cb, values, is_type in ((self.cb_topic, topics, False),
                                    (self.cb_key, keys, False),
                                    (self.cb_type, types, True)):
            cb.blockSignals(True); cb.clear(); cb.addItem("(all)", "")
            for v in values:
                cb.addItem(_short_type(v) if is_type else v, v)
            cb.blockSignals(False)
        self.seq_min.setValue(0); self.seq_max.setRange(0, max(max_seq, 1)); self.seq_max.setValue(max_seq)

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


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pulse-sample.jsonl"
    if not Path(path).exists():
        er.make_sample(path)   # convenience for a first run
    app = QApplication(sys.argv)
    win = MainWindow(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
