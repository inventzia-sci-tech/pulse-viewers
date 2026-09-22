# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""The Pulse output layout, in one place (Python side).

Path convention (see pulse-output.md):

    $PULSE_OUTPUT / {historical|live} / <app> / <runId> / {run.json, events.jsonl, console.log}

The viewer and Python tools import this to resolve the output root, build run directories, and
read/write/list run manifests. A matching Java helper does the same on the engine side, so both
agree byte-for-byte on the directory shape and manifest.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

MANIFEST = "run.json"
EVENTS = "events.jsonl"
CONSOLE = "console.log"

_TIER_BY_MODE = {"COMPRESSED_TIME": "historical", "REAL_TIME": "live", "MIXED": "live"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_COMPONENT = 64


def output_root(explicit: str | os.PathLike | None = None) -> Path:
    """Resolve the output root: explicit path, else $PULSE_OUTPUT, else ~/.pulse/runs."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("PULSE_OUTPUT")
    return Path(env) if env else Path.home() / ".pulse" / "runs"


def tier_for_mode(mode: str) -> str:
    """Map an engine OperatingMode name to its directory tier (historical / live)."""
    return _TIER_BY_MODE.get(mode, "other")


def _safe(name: str) -> str:
    """A single safe path component: keep letters/digits/._-; collapse the rest to '-'; strip leading
    and trailing dots/dashes; bound the length; never a separator, '.', '..', or empty."""
    s = _SAFE.sub("-", (name or "").strip()).strip("-.")[:_MAX_COMPONENT].strip("-.")
    return s if s not in ("", ".", "..") else "unnamed"


def _is_within(root: Path, target: Path) -> bool:
    """True if target resolves inside root (defence against traversal via a crafted app name)."""
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def new_run_id() -> str:
    """A sortable, unique run id: <UTC timestamp>Z-<short uuid>."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]


def run_dir(root: str | os.PathLike, mode: str, app: str, run_id: str) -> Path:
    """The directory for one run: root / tier(mode) / app / run_id."""
    return Path(root) / tier_for_mode(mode) / _safe(app) / run_id


@dataclass(frozen=True)
class RunPaths:
    dir: Path
    manifest: Path
    events: Path
    console: Path


def create_run(mode: str, app: str, *, source: str = "", window: tuple[int, int] | None = None,
               type_fingerprint: str | None = None, provider_ids: list[str] | None = None,
               root: str | os.PathLike | None = None, run_id: str | None = None) -> RunPaths:
    """Create a run directory and write the initial manifest. Collision-safe (the run directory must
    be freshly created; a generated-id collision retries, a caller-supplied id collision errors) and
    path-safe (the resolved directory must be under the output root). Returns its paths."""
    base = output_root(root)
    d = None
    for _ in range(8):
        rid = run_id or new_run_id()
        candidate = run_dir(base, mode, app, rid)
        if not _is_within(base, candidate):
            raise ValueError(f"unsafe run directory outside the output root: {candidate}")
        candidate.parent.mkdir(parents=True, exist_ok=True)   # tiers may pre-exist
        try:
            candidate.mkdir(exist_ok=False)                   # the run dir itself must be fresh
        except FileExistsError:
            if run_id is not None:
                raise FileExistsError(f"run directory already exists: {candidate}")  # never overwrite
            continue                                          # generated-id collision: try a new id
        d = candidate
        break
    if d is None:
        raise RuntimeError("could not allocate a unique run directory")
    paths = RunPaths(d, d / MANIFEST, d / EVENTS, d / CONSOLE)
    write_manifest(paths.dir, {
        "runId": rid, "app": app, "mode": mode, "tier": tier_for_mode(mode), "source": source,
        "createdAt": datetime.now(timezone.utc).isoformat(), "endedAt": None,
        "window": {"start": window[0], "end": window[1]} if window else None,
        "typeFingerprint": type_fingerprint, "providerIds": provider_ids or [],
        "runStatus": "running", "recordingStatus": "recording", "counts": None,
        "artifacts": {"manifest": MANIFEST, "events": EVENTS, "console": CONSOLE},
    })
    return paths


def finalize_run(run_directory: str | os.PathLike, run_status: str,
                 recording_status: str | None = None, counts: dict | None = None) -> dict:
    """Finalize a run's manifest. ``run_status`` is the engine outcome; ``recording_status`` and
    ``counts`` come from the recorder after it has drained and closed, so publish them here, not when
    the engine returns. Engine and recording outcomes are kept separate on purpose: either can fail
    while the other succeeds."""
    d = Path(run_directory)
    m = read_manifest(d) or {}
    m["runStatus"] = run_status
    if recording_status is not None:
        m["recordingStatus"] = recording_status
    if counts is not None:
        m["counts"] = counts
    m["endedAt"] = datetime.now(timezone.utc).isoformat()
    write_manifest(d, m)
    return m


def write_manifest(run_directory: str | os.PathLike, manifest: dict) -> None:
    """Atomically write run.json: one writer, temp file, fsync, then os.replace, so a reader or a
    crash mid-write never sees a half-written manifest."""
    d = Path(run_directory)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, d / MANIFEST)   # atomic on POSIX and Windows


def read_manifest(run_directory: str | os.PathLike) -> dict | None:
    p = Path(run_directory) / MANIFEST
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def list_runs(root: str | os.PathLike | None = None) -> list[dict]:
    """All runs under the root, newest first. Each entry is the manifest plus a 'dir' path.

    Robust to a missing manifest (a run dir with only events.jsonl still lists, minimally)."""
    base = output_root(root)
    runs: list[dict] = []
    if not base.is_dir():
        return runs
    for manifest_path in base.glob(f"*/*/*/{MANIFEST}"):
        m = read_manifest(manifest_path.parent) or {}
        m.setdefault("runId", manifest_path.parent.name)
        m["dir"] = str(manifest_path.parent)
        runs.append(m)
    # also surface run dirs that have events but no manifest
    for events_path in base.glob(f"*/*/*/{EVENTS}"):
        if (events_path.parent / MANIFEST).exists():
            continue
        runs.append({"runId": events_path.parent.name, "dir": str(events_path.parent),
                     "status": "unknown", "app": events_path.parent.parent.name})
    runs.sort(key=lambda r: r.get("runId", ""), reverse=True)  # timestamp-first id => newest first
    return runs


if __name__ == "__main__":
    print("output root:", output_root())
    for r in list_runs():
        print(f"  {r.get('tier','?'):10} {r.get('app','?'):22} "
              f"run={r.get('runStatus','?'):9} rec={r.get('recordingStatus','?'):9} {r['runId']}")
