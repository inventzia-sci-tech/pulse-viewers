# Changelog

All notable changes to pulse-viewers are recorded here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-23

First release. pulse-viewers hosts the desktop viewers built over the artifacts a Pulse run leaves
behind; this release contains one, the Events Viewer, plus the shared pieces any further viewer
builds on.

### Added

- **Events Viewer** (`pulse-events-viewer`): reads a run's `events.jsonl`, validates it against the
  record schema, and shows the dispatch stream in a sortable, filterable grid with per-type colour
  and a payload detail tree.
- **Run browser**: lists every run under the Pulse output root and classifies each one's health
  (`ok` / `partial` / `broken` / `running` / `unknown`) from its manifest, keeping the engine
  outcome and the recording outcome separate so a partial capture is never shown as complete. The
  output folder is chosen in the UI and remembered. Runs can be deleted, and each opens in its own
  detached window while the browser stays available.
- **Live following**: a run still being written is followed as it grows, handling a partial trailing
  line, truncation, rotation and malformed records; it stops by itself when the recorder writes its
  trailer. `live_tail` is Qt-free and usable on its own.
- **Contract readers** (`inventzia.pulse.viewers.contract`): the Python side of the run output
  layout and the recording envelope, with the record JSON Schema shipped as package data. Twins of
  the Java implementations in pulse-beacon.

### Notes

- Deliberately independent of pulse-data and pulse-beacon: a viewer reads a run's artifacts as plain
  JSON and never links the engine, so the only dependencies are PySide6 and jsonschema.
