#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
#
# This file is part of pulse-viewers.
#
# pulse-viewers is dual-licensed:
#   - Under the GNU Affero General Public License v3.0 or later (see LICENSE-AGPL-3.0).
#   - Under a commercial license (see LICENSE-COMMERCIAL.txt).
#     Contact operations@inventzia.com.
#
# Prove the *distribution* works, not the source tree. Installs the built wheel into a throwaway
# environment and exercises it from a directory with no source tree in sight, so anything that
# silently depends on the checkout -- a source-relative path to the schema, a module that only
# imports because the repo root happens to be on sys.path -- fails here rather than in a user's
# install.
#
#   bash dist-smoke-test.sh [path/to/dist]     # default: ./dist
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="${1:-$HERE/dist}"
PYTHON="${PYTHON:-python3}"

fail() { echo "  ❌ $1" >&2; exit 1; }
ok()   { echo "  ✅ $1"; }

WHEEL="$(ls "$DIST"/pulse_viewers-*.whl 2>/dev/null | head -1 || true)"
[ -n "$WHEEL" ] || fail "no wheel in $DIST -- run release-build.sh (or python -m build) first"
SDIST="$(ls "$DIST"/pulse_viewers-*.tar.gz 2>/dev/null | head -1 || true)"
[ -n "$SDIST" ] || fail "no sdist in $DIST"
echo "==> wheel: $(basename "$WHEEL")"
echo "==> sdist: $(basename "$SDIST")"

# The wheel must carry the record schema and the licence files, and must NOT carry an __init__.py at
# the namespace levels (that would shadow pulse-data / pulse-beacon in inventzia.pulse.*).
"$PYTHON" - "$WHEEL" <<'PY'
import sys, zipfile
names = set(zipfile.ZipFile(sys.argv[1]).namelist())
required = {
    "inventzia/pulse/viewers/contract/event-record.schema.json",
    "pulse_viewers-0.1.0.dist-info/licenses/LICENSE-AGPL-3.0",
}
missing = [n for n in required if n not in names]
# Version-agnostic check for the licence files, so this survives a version bump.
if any("dist-info/licenses/LICENSE-AGPL-3.0" in n for n in names):
    missing = [m for m in missing if "LICENSE-AGPL-3.0" not in m]
for want in ("LICENSE-COMMERCIAL.txt", "NOTICE"):
    if not any(f"dist-info/licenses/{want}" in n for n in names):
        missing.append(want)
forbidden = [n for n in names if n in ("inventzia/__init__.py", "inventzia/pulse/__init__.py")]
if missing:
    raise SystemExit(f"  ❌ wheel is missing: {missing}")
if forbidden:
    raise SystemExit(f"  ❌ wheel breaks the inventzia.pulse namespace: {forbidden}")
print("  ✅ wheel contents: schema, licences, namespace packages intact")
PY

VENV="$(mktemp -d)/venv"
echo "==> installing the wheel into $VENV"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$WHEEL"

# Run from a scratch directory: the checkout must be irrelevant.
SCRATCH="$(mktemp -d)"
echo "==> exercising the installed package from $SCRATCH"
( cd "$SCRATCH" && QT_QPA_PLATFORM=offscreen "$VENV/bin/python" - <<'PY'
import pathlib, tempfile

import inventzia.pulse.viewers as viewers
from inventzia.pulse.viewers import event_viewer, live_tail, run_browser   # noqa: F401
from inventzia.pulse.viewers.contract import event_record, run_layout      # noqa: F401

here = pathlib.Path(viewers.__file__).resolve()
assert "site-packages" in here.parts, f"imported from the source tree, not the install: {here}"

# The schema must come from package data, not a source-relative path.
schema = event_record.load_schema()
assert set(schema["$defs"]) >= {"header", "event", "trailer"}, sorted(schema)

# A recording round-trips through the installed reader and validates.
out = pathlib.Path(tempfile.mkdtemp()) / "sample.jsonl"
event_record.make_sample(out, events=25)
report = event_record.validate(out)
assert report.ok and report.event_count == 25, (report.ok, report.diagnostics)

# The output layout resolves and lists an empty root without complaint.
root = pathlib.Path(tempfile.mkdtemp())
assert run_layout.list_runs(root) == []

# And the GUI constructs headless against a real recording.
from PySide6.QtWidgets import QApplication
QApplication([])
win = event_viewer.MainWindow(str(out), None, None)
assert win.model.rowCount() == 25, win.model.rowCount()

print(f"  ✅ installed package works: pulse-viewers {viewers.__version__}")
PY
)

# The console entry point must exist and run. --help is the only invocation that returns instead of
# opening a window, so it is the one that can be checked here.
[ -x "$VENV/bin/pulse-events-viewer" ] || fail "console script pulse-events-viewer was not installed"
( cd "$SCRATCH" && "$VENV/bin/pulse-events-viewer" --help >/dev/null ) \
    || fail "pulse-events-viewer --help failed"
ok "console entry point resolves and runs"

echo "✅ distribution smoke test passed"
