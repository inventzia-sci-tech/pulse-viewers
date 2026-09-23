# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""Follow a recording that is still being written.

The reader half of live following (viewer.md section 5), shared by every viewer and deliberately
free of Qt so it can be driven by a timer, a thread, or a test.

Tailing a file another process is appending to is not just "read the new bytes". Four things go
wrong, and each is handled here rather than left to the caller:

* **A partial trailing line.** A read can land mid-record. The incomplete tail is buffered and only
  parsed once its newline arrives, so a record is never parsed twice or half-parsed once.
* **Truncation and rotation.** If the file shrinks, or its identity changes underneath the path, the
  follower re-opens by path and starts again rather than reading from a stale offset into unrelated
  bytes. Detection is by size going backwards and, where the platform reports it, by inode.
* **Malformed records.** A bad line is counted and skipped. A recording written by a crashed or
  failing recorder is precisely what someone is watching for, so a parse error must never end the
  follow.
* **A file that does not exist yet.** Following can begin before the run has opened its recording.

Nothing here blocks: `poll()` returns what is available now, and the caller decides how often to
call it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FollowStats:
    """What the follow has seen so far. Surfaced in the UI, so a viewer can be honest about loss."""

    records: int = 0            # complete records parsed (header + events + trailer)
    events: int = 0             # 'event' records specifically
    skipped: int = 0            # lines that were not valid JSON, or not a record object
    reopened: int = 0           # truncation/rotation events survived
    bytes_read: int = 0         # payload bytes consumed since the follow began
    header: dict | None = None  # the recording's header, once seen
    trailer: dict | None = None # the trailer, once seen: the recorder said it was done
    missing: bool = True        # the file does not exist (yet)

    @property
    def complete(self) -> bool:
        """Has the recorder written its trailer? Then there is nothing more to follow."""
        return self.trailer is not None

    def summary(self) -> str:
        parts = [f"{self.events} events"]
        if self.skipped:
            parts.append(f"{self.skipped} unreadable line(s)")
        if self.reopened:
            parts.append(f"re-opened {self.reopened}x")
        if self.complete:
            parts.append(f"recording {self.trailer.get('status', 'complete')}")
        return ", ".join(parts)


@dataclass
class RecordingFollower:
    """Incremental reader over one recording file.

    Call `poll()` repeatedly; each call returns the event records that have become complete since
    the last call. Header and trailer are captured in `stats` rather than returned, because a caller
    appending rows only wants the events.
    """

    path: Path
    _fh: object = field(default=None, repr=False)
    _pos: int = 0
    _buf: str = ""
    _ident: tuple | None = None
    stats: FollowStats = field(default_factory=FollowStats)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ------------------------------------------------------------------
    def poll(self) -> list[dict]:
        """Read whatever has been appended since last time; return newly complete event records."""
        try:
            st = os.stat(self.path)
        except (OSError, ValueError):
            # Not there yet, or vanished under us. Drop any handle and wait; this is a normal state
            # when following a run that has not opened its recording.
            self._close()
            self.stats.missing = True
            return []

        self.stats.missing = False
        ident = self._identity(st)

        # Rotation or truncation: the bytes at our offset are no longer the bytes we were reading.
        if self._fh is not None and (st.st_size < self._pos or
                                     (ident is not None and ident != self._ident)):
            self._reopen()

        if self._fh is None:
            self._open(ident)

        chunk = self._fh.read()
        if not chunk:
            return []
        self._pos = self._fh.tell()
        self.stats.bytes_read += len(chunk)
        return self._consume(chunk)

    # ------------------------------------------------------------------
    def _consume(self, chunk: str) -> list[dict]:
        """Split on newlines, keeping an incomplete trailing line for next time."""
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")   # the tail is whatever follows the last newline
        events: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue                             # blank lines are not records, and not errors
            record = self._parse(line)
            if record is None:
                self.stats.skipped += 1
                continue
            self.stats.records += 1
            kind = record.get("kind")
            if kind == "event":
                self.stats.events += 1
                events.append(record)
            elif kind == "header":
                self.stats.header = record
            elif kind == "trailer":
                self.stats.trailer = record
            else:
                # A well-formed object that is not a record kind we know: count it as unreadable
                # rather than silently dropping it.
                self.stats.records -= 1
                self.stats.skipped += 1
        return events

    @staticmethod
    def _parse(line: str) -> dict | None:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            return None
        return record if isinstance(record, dict) else None

    # ------------------------------------------------------------------
    def _identity(self, st) -> tuple | None:
        """(device, inode) when the platform reports one; None when it does not.

        Windows populates st_ino on NTFS but can report 0 elsewhere, so an absent identity is not an
        error -- it just means rotation is detected by size alone.
        """
        ino = getattr(st, "st_ino", 0)
        return (st.st_dev, ino) if ino else None

    def _open(self, ident: tuple | None) -> None:
        # newline="" keeps \r\n intact so byte offsets stay honest on Windows; the parser strips it.
        self._fh = open(self.path, "r", encoding="utf-8", errors="replace", newline="")
        self._fh.seek(self._pos)
        self._ident = ident

    def _reopen(self) -> None:
        """Start the file again from the beginning: its contents are no longer what we were reading."""
        self._close()
        self._pos = 0
        self._buf = ""
        self.stats.reopened += 1

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def close(self) -> None:
        self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._close()
        return False


def follow_once(path) -> tuple[list[dict], FollowStats]:
    """Read a whole recording in one pass with the follower's tolerance for damage.

    Useful for opening a file that a crashed recorder left without a trailer, where the strict
    reader would rather report an error than show what survived.
    """
    follower = RecordingFollower(Path(path))
    events = follower.poll()
    follower.close()
    return events, follower.stats
