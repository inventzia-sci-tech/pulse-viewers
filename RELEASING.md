# Releasing pulse-viewers

pulse-viewers releases on its own cadence. It depends on neither pulse-data nor pulse-beacon — a
viewer reads a run's artifacts as plain JSON — so there is no lockstep version, no sibling checkout,
and no Java build. That makes this considerably shorter than
[pulse-beacon's RELEASING.md](https://github.com/inventzia-sci-tech/pulse-beacon/blob/main/RELEASING.md).

## The version lives in three places

| where | what it is |
|---|---|
| `pyproject.toml` `[project].version` | what PyPI publishes |
| `src/inventzia/pulse/viewers/__init__.py` `__version__` | what the installed package reports |
| `CHANGELOG.md` top heading | what the release claims to contain |

`check-versions.sh` asserts they agree, and `--release` additionally refuses a pre-release suffix or
an `Unreleased` heading and checks the tag. A release that disagrees with itself is worse than one
that is merely late.

## Steps

1. **Land the work.** `main` green in CI.

2. **Bump the version** in `pyproject.toml` and `__init__.py`, and write the `CHANGELOG.md` entry
   under a `## [X.Y.Z] - YYYY-MM-DD` heading.

   ```bash
   bash check-versions.sh --release --tag vX.Y.Z
   ```

3. **Build and verify the artifacts.**

   ```bash
   bash release-build.sh --tag vX.Y.Z
   ```

   This cleans, builds the sdist and wheel, then runs `dist-smoke-test.sh`: the wheel is installed
   into a throwaway environment and exercised **from a scratch directory**, so anything that
   silently depends on the checkout fails here rather than in a user's install. It also checks the
   wheel carries the record schema and the licence files, and carries no `__init__.py` at
   `inventzia/` or `inventzia/pulse/` — one there would shadow pulse-data and pulse-beacon in the
   shared namespace.

4. **Check the PyPI page renders.** The README is the project's landing page, and relative links
   resolve against nothing there — every link must be an absolute URL.

   ```bash
   python -m twine check dist/*
   ```

5. **Tag and push.**

   ```bash
   git tag -a vX.Y.Z -m "pulse-viewers X.Y.Z"
   git push origin vX.Y.Z
   ```

   The tag triggers CI's `release` job, which repeats the version check against the tag and the
   distribution smoke test, then uploads the artifacts for download. It deliberately does **not**
   publish.

6. **Publish** — a credentialed step run by the maintainer, against the artifacts from step 3:

   ```bash
   python -m twine upload dist/*
   ```

7. **Back to development.** Bump `pyproject.toml` and `__init__.py` to the next version and open an
   `## [Unreleased]` changelog heading.

## Notes

- **Qt is not bundled.** PySide6 is an ordinary dependency, so the wheel is pure Python and
  platform-independent. Its LGPL obligations attach to *distributing* Qt, which declaring a
  dependency does not do — see the packaging note in the README before ever shipping a frozen
  build.
- **No publish workflow.** Uploading is intentionally manual and credentialed; CI produces and
  verifies artifacts, a human ships them.
