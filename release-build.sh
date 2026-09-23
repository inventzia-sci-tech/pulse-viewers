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
# Build the release artifacts, with the checks that must not be skipped:
#
#   1. versions agree (pyproject / __version__ / CHANGELOG / tag)   -- check-versions.sh --release
#   2. a clean dist/ and build/, so nothing stale is republished
#   3. sdist + wheel
#   4. the wheel is installed and exercised from a scratch directory -- dist-smoke-test.sh
#
#   bash release-build.sh [output-dir] [--tag vX.Y.Z]
#
# Publishing is deliberately NOT here: uploading is a credentialed step the maintainer runs against
# the artifacts this produces.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
OUT="$HERE/dist"
TAG=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)
            [ "$#" -ge 2 ] || { echo "--tag requires a value" >&2; exit 2; }
            TAG="$2"; shift 2 ;;
        *) OUT="$1"; shift ;;
    esac
done

echo "==> version consistency"
if [ -n "$TAG" ]; then
    bash "$HERE/check-versions.sh" --release --tag "$TAG"
else
    bash "$HERE/check-versions.sh" --release
fi

echo "==> clean"
rm -rf "$HERE/dist" "$HERE/build" "$HERE"/src/*.egg-info

echo "==> build sdist + wheel"
"$PYTHON" -m pip install --quiet --upgrade build
"$PYTHON" -m build --outdir "$OUT" "$HERE"

echo "==> distribution smoke test"
PYTHON="$PYTHON" bash "$HERE/dist-smoke-test.sh" "$OUT"

echo
echo "✅ release artifacts in $OUT"
ls -1 "$OUT"
echo
echo "Next (credentialed, run by the maintainer):"
echo "    python -m twine check $OUT/*"
echo "    python -m twine upload $OUT/*"
