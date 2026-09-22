# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Reference reader/writer for the Pulse event-recording envelope (v1).

The Python side of the recording contract in ``pulse-viewer``, mirroring the Java
``EventRecorderGateway``. Lets the viewer be built and tested against real recordings without a JVM
or any domain adapter. The schema is ``pulse-viewer/schema/event-record.schema.json``.

A recording is JSONL: one ``header`` line, then ``event`` lines ordered by increasing ``seq``, then
a ``trailer`` line with completion status and accounting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ENVELOPE_VERSION = 1
SUPPORTED_VERSIONS = {1}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------------------------------

def header(run_id: str, operating_mode: str, window_start: int, window_end: int,
           type_fingerprint: str | None = None, provider_ids: list[str] | None = None,
           source: str | None = None, recorder: str = "pulse-viewer/py-ref") -> dict[str, Any]:
    rec: dict[str, Any] = {
        "kind": "header", "v": ENVELOPE_VERSION, "runId": run_id, "recorder": recorder,
        "recordedAt": _now_iso(), "operatingMode": operating_mode,
        "window": {"start": window_start, "end": window_end},
        "typeFingerprint": type_fingerprint, "providerIds": provider_ids or [],
    }
    if source is not None:
        rec["source"] = source
    return rec


def event(run_id: str, seq: int, event_time: int, topic: str, key: str, type_id: str,
          payload: dict[str, Any], observed_at: str | None = None) -> dict[str, Any]:
    return {
        "kind": "event", "v": ENVELOPE_VERSION, "runId": run_id, "seq": seq,
        "observedAt": observed_at or _now_iso(), "eventTime": event_time,
        "topic": topic, "key": key, "typeId": type_id, "payload": payload,
    }


def trailer(run_id: str, observed: int, events: int, overflow: int = 0,
            serialization_errors: int = 0, abandoned: int = 0,
            status: str = "complete") -> dict[str, Any]:
    return {"kind": "trailer", "v": ENVELOPE_VERSION, "runId": run_id, "status": status,
            "observed": observed, "events": events, "overflow": overflow,
            "serializationErrors": serialization_errors, "abandoned": abandoned}


# --------------------------------------------------------------------------------------------------
# Recorder (synchronous; single write path, so overflow/serializationErrors are always 0)
# --------------------------------------------------------------------------------------------------

@dataclass
class Recorder:
    path: Path
    run_id: str
    _fh: Any = field(default=None, init=False, repr=False)
    _seq: int = field(default=0, init=False, repr=False)
    _written: int = field(default=0, init=False, repr=False)

    def open(self, operating_mode: str, window_start: int, window_end: int,
             type_fingerprint: str | None = None, provider_ids: list[str] | None = None,
             source: str | None = None) -> "Recorder":
        if self._fh is not None:
            raise RuntimeError("Recorder already open")
        self._seq = 0           # reset state on (re)open so counts match the new file
        self._written = 0
        self._fh = open(self.path, "w", encoding="utf-8")
        self._write(header(self.run_id, operating_mode, window_start, window_end,
                           type_fingerprint, provider_ids, source))
        return self

    def record(self, topic: str, key: str, type_id: str, payload: dict[str, Any],
               event_time: int, observed_at: str | None = None) -> int:
        s = self._seq
        self._seq += 1
        self._write(event(self.run_id, s, event_time, topic, key, type_id, payload, observed_at))
        self._written += 1
        self._fh.flush()        # reference recorder favours live latency over throughput
        return s

    def close(self, status: str = "complete") -> None:
        if self._fh is None:
            return
        # synchronous writer: observed == written, no overflow, no serialization errors
        self._write(trailer(self.run_id, observed=self._written, events=self._written,
                            status=status))
        self._fh.flush()
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        self.close(status="failed" if exc_type is not None else "complete")

    def _write(self, obj: dict[str, Any]) -> None:
        self._fh.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
        self._fh.write("\n")


# --------------------------------------------------------------------------------------------------
# Reader / validator (enforces the contract, returns line-numbered diagnostics)
# --------------------------------------------------------------------------------------------------

@dataclass
class Report:
    ok: bool
    diagnostics: list[tuple[int, str]]      # (line number, message)
    header: dict[str, Any] | None
    trailer: dict[str, Any] | None
    event_count: int
    run_id: str | None


_REQUIRED = {
    "header": ("v", "runId", "operatingMode", "window"),
    "event": ("v", "runId", "seq", "eventTime", "topic", "key", "typeId", "payload"),
    "trailer": ("v", "runId", "status", "observed", "events"),
}


_OPERATING_MODES = ("COMPRESSED_TIME", "REAL_TIME", "UNDEFINED")
_STATUSES = ("complete", "failed", "interrupted")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)  # JSON booleans are not integers


def _check_record(rec: Any) -> str | None:
    """Structural validation of one record; never raises. Unknown datum typeIds are fine; bad
    envelopes are not. Used by :func:`read`; :func:`validate` prefers the JSON Schema when present."""
    if not isinstance(rec, dict):
        return f"not a JSON object ({type(rec).__name__})"
    kind = rec.get("kind")
    if kind not in _REQUIRED:
        return f"unknown or missing 'kind': {kind!r}"
    for f in _REQUIRED[kind]:
        if f not in rec:
            return f"{kind} missing required field: {f}"
    if not _is_int(rec.get("v")) or rec["v"] not in SUPPORTED_VERSIONS:
        return f"unsupported or invalid version: {rec.get('v')!r}"
    rid = rec.get("runId")
    if not isinstance(rid, str) or not rid:
        return "runId must be a non-empty string"
    if kind == "header":
        if rec.get("operatingMode") not in _OPERATING_MODES:
            return f"invalid operatingMode: {rec.get('operatingMode')!r}"
        w = rec.get("window")
        if not isinstance(w, dict) or not _is_int(w.get("start")) or not _is_int(w.get("end")):
            return "window must have integer start and end"
    elif kind == "event":
        if not _is_int(rec.get("seq")):
            return "event 'seq' must be an integer"
        if not _is_int(rec.get("eventTime")):
            return "event 'eventTime' must be an integer"
        for f in ("topic", "key", "typeId"):
            if not isinstance(rec.get(f), str):
                return f"event '{f}' must be a string"
        if not isinstance(rec.get("payload"), dict):
            return "event 'payload' must be an object"
    elif kind == "trailer":
        if rec.get("status") not in _STATUSES:
            return f"invalid trailer status: {rec.get('status')!r}"
        for f in ("observed", "events"):
            if not _is_int(rec.get(f)) or rec[f] < 0:
                return f"trailer '{f}' must be a nonnegative integer"
        for f in ("overflow", "serializationErrors", "abandoned"):
            if f in rec and (not _is_int(rec[f]) or rec[f] < 0):
                return f"trailer '{f}' must be a nonnegative integer"
    return None


def _record_checker(schema: dict | None):
    """Return a ``rec -> error-or-None`` function. Uses the JSON Schema via jsonschema when available
    (the authoritative check), else the structural fallback. Never raises on a bad record."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _check_record
    if schema is None:
        schema_path = Path(__file__).resolve().parent.parent / "schema" / "event-record.schema.json"
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
    full = jsonschema.Draft202012Validator(schema)
    # Per-kind validators give a precise field message instead of the whole-record "not valid under
    # any of the given schemas" that the top-level oneOf would produce.
    by_kind = {k: jsonschema.Draft202012Validator(defn)
               for k, defn in schema.get("$defs", {}).items()}

    def check(rec: Any) -> str | None:
        v = by_kind.get(rec.get("kind")) if isinstance(rec, dict) else None
        v = v or full
        errors = sorted(v.iter_errors(rec), key=lambda e: list(e.absolute_path))
        if not errors:
            return None
        e = errors[0]
        loc = "/".join(str(p) for p in e.absolute_path)
        kind = rec.get("kind") if isinstance(rec, dict) else "?"
        return f"{kind}{('.' + loc) if loc else ''}: {e.message}"

    return check


def validate(path: str | Path, schema: dict | None = None) -> Report:
    """Validate a recording and return line-numbered diagnostics.

    Each record is validated against the supplied JSON Schema (or a structural fallback) before any
    file-level check, so a malformed record cannot crash the file-level logic. File-level: header
    present, first, and once; single runId; strictly increasing event seq; trailer last and once; and
    the accounting identity ``observed == events + overflow + serializationErrors + abandoned`` with
    the written-event count.
    """
    check = _record_checker(schema)
    diags: list[tuple[int, str]] = []
    hdr: dict[str, Any] | None = None
    trl: dict[str, Any] | None = None
    run_id: str | None = None
    events = 0
    last_seq: int | None = None

    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                diags.append((lineno, f"malformed JSON: {e.msg}"))
                continue
            err = check(rec)
            if err:
                diags.append((lineno, err))
                continue  # not schema-valid; skip file-level checks (prevents type errors)

            kind = rec["kind"]
            if run_id is None:
                run_id = rec["runId"]
            elif rec["runId"] != run_id:
                diags.append((lineno, f"runId {rec['runId']!r} differs from {run_id!r}"))

            if kind == "header":
                if hdr is not None:
                    diags.append((lineno, "second header record"))
                elif events or trl is not None:
                    diags.append((lineno, "header must be the first record"))
                hdr = rec
            elif kind == "event":
                if trl is not None:
                    diags.append((lineno, "event after trailer"))
                if last_seq is not None and rec["seq"] <= last_seq:
                    diags.append((lineno, f"seq {rec['seq']} not increasing (previous {last_seq})"))
                last_seq = rec["seq"]
                events += 1
            elif kind == "trailer":
                if trl is not None:
                    diags.append((lineno, "second trailer record"))
                trl = rec

    if hdr is None:
        diags.append((0, "no header record"))
    if trl is not None:
        ev = trl["events"]
        obs = trl["observed"]
        ovf = trl.get("overflow", 0)
        ser = trl.get("serializationErrors", 0)
        ab = trl.get("abandoned", 0)
        if ev != events:
            diags.append((0, f"trailer events={ev} but file has {events} event records"))
        if obs != ev + ovf + ser + ab:
            diags.append((0, "trailer accounting broken: observed=%d != events(%d)+overflow(%d)+serializationErrors(%d)+abandoned(%d)=%d"
                          % (obs, ev, ovf, ser, ab, ev + ovf + ser + ab)))
    return Report(ok=not diags, diagnostics=diags, header=hdr, trailer=trl,
                  event_count=events, run_id=run_id)


def read(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield only the valid ``event`` records. For contract enforcement and diagnostics use
    :func:`validate`. For live following add partial-line, truncation, and rotation handling
    (viewer.md, section 5)."""
    for lineno, raw in enumerate(open(path, "r", encoding="utf-8"), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("kind") == "event" and _check_record(rec) is None:
            yield rec


# --------------------------------------------------------------------------------------------------
# Sample recording (neutral demo types, so it stays self-consistent and needs no adapter)
# --------------------------------------------------------------------------------------------------

def make_sample(path: str | Path, events: int = 200) -> Path:
    """Emit a small demo recording for offline viewer development.

    Uses neutral ``com.example.demo.*`` types so the payloads are self-consistent and make no false
    claim to decode as real pulse-data types; the fingerprint is null because this is not a real run.
    """
    path = Path(path)
    run_id = "sample-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    base = 1_283_630_000_000
    types = [
        ("demo.bars", "AAPL", "com.example.demo.Bar",
         lambda i: {"symbol": "AAPL", "time": base + i * 1000, "open": 1.10, "high": 1.30,
                    "low": 1.05, "close": round(1.10 + (i % 20) * 0.01, 2), "volume": 100 + i}),
        ("demo.messages", "sys", "com.example.demo.Message",
         lambda i: {"channel": "sys", "time": base + i * 1000, "text": f"message {i}"}),
        ("demo.heartbeat", "beat", "com.example.demo.Heartbeat",
         lambda i: {"id": "beat", "time": base + i * 1000}),
    ]
    with Recorder(path, run_id).open("COMPRESSED_TIME", base, base + events * 1000,
                                     type_fingerprint=None, provider_ids=[],
                                     source="event_record.make_sample") as rec:
        for i in range(events):
            topic, key, type_id, mk = types[i % len(types)]
            rec.record(topic, key, type_id, mk(i), event_time=base + i * 1000)
    return path


if __name__ == "__main__":
    import sys
    import tempfile
    # The default lands in the platform's own temp directory: "/tmp" is not a path on Windows,
    # where it resolves to C:\tmp and is not created.
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(tempfile.gettempdir()) / "pulse-sample.jsonl")
    p = make_sample(out)
    rep = validate(p)
    print(f"wrote {p}: events={rep.event_count} ok={rep.ok} status={rep.trailer and rep.trailer['status']}")
    for lineno, msg in rep.diagnostics:
        print(f"  line {lineno}: {msg}")
