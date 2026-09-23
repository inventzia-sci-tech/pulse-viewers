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
# Version consistency. pulse-viewers has no Maven side and pins no sibling package, so the coupling
# is narrower than pulse-beacon's -- but the version still appears in three places, and a release
# that disagrees with itself is worse than one that is merely late:
#
#   pyproject.toml [project].version   the version PyPI publishes
#   src/.../viewers/__init__.py        __version__, what the installed package reports
#   CHANGELOG.md                       the top release heading
#
#   bash check-versions.sh [--release] [--tag vX.Y.Z]
#
# --release additionally refuses a pre-release/dev suffix and an "Unreleased" changelog heading.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE="${PULSE_RELEASE:-}"
TAG=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --release) RELEASE=1; shift ;;
        --tag)
            [ "$#" -ge 2 ] || { echo "--tag requires a value" >&2; exit 2; }
            TAG="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

fail() { echo "  ❌ $1" >&2; exit 1; }
ok()   { echo "  ✅ $1"; }

PYPROJECT="$HERE/pyproject.toml"
INIT="$HERE/src/inventzia/pulse/viewers/__init__.py"
CHANGELOG="$HERE/CHANGELOG.md"

PROJ_V=$(grep -m1 -oE '^version = "[^"]+"' "$PYPROJECT" | sed -E 's/version = "//; s/"//')
INIT_V=$(grep -m1 -oE '^__version__ = "[^"]+"' "$INIT"   | sed -E 's/__version__ = "//; s/"//')
CHLOG_V=$(grep -m1 -oE '^## \[[^]]+\]' "$CHANGELOG" | sed -E 's/^## \[//; s/\]//')

echo "pulse-viewers: pyproject=$PROJ_V  __version__=$INIT_V  changelog=$CHLOG_V"

[ -n "$PROJ_V" ]  || fail "no version in pyproject.toml"
[ -n "$INIT_V" ]  || fail "no __version__ in $INIT"
[ -n "$CHLOG_V" ] || fail "no release heading in CHANGELOG.md"

[ "$PROJ_V" = "$INIT_V" ] || fail "pyproject version $PROJ_V != __version__ $INIT_V (the installed package would misreport itself)"

if [ -n "$RELEASE" ]; then
    echo "== release mode: final version, changelog entry present, tag agrees =="
    case "$PROJ_V" in
        *a*|*b*|*rc*|*.dev*) fail "release build carries a pre-release version ($PROJ_V)";;
    esac
    [ "$CHLOG_V" != "Unreleased" ] || fail "CHANGELOG.md still has an Unreleased heading; name the release"
    [ "$CHLOG_V" = "$PROJ_V" ] || fail "CHANGELOG.md documents $CHLOG_V but the package is $PROJ_V"
    if [ -n "$TAG" ]; then
        [ "$TAG" = "v$PROJ_V" ] || fail "release tag $TAG does not match package version v$PROJ_V"
    fi
    ok "release versions consistent: pulse-viewers=$PROJ_V (changelog and tag agree)"
else
    echo "== dev mode: pyproject and __version__ agree =="
    [ "$CHLOG_V" = "$PROJ_V" ] || echo "  ⚠️  CHANGELOG top entry is $CHLOG_V, package is $PROJ_V (fine mid-development)"
    ok "dev versions aligned: pulse-viewers=$PROJ_V"
fi
