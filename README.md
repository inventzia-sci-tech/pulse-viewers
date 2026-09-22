# pulse-viewers

**Inventzia's Pulse Run Viewer**

Viewing utilities for Pulse applications.

This repository hosts the viewers built over the artifacts a Pulse run leaves behind. It is a home
for several of them; today it holds one, the **Events Viewer**, with the shared contracts and
readers that any further viewer builds on. The design rationale is in
[`../viewer.md`](../viewer.md).

## What a Pulse viewer is

Every viewer here follows the same three rules. They are what keep a viewer from becoming a second
engine.

**1. A viewer is an out-of-process reader.** It never links pulse-beacon, never starts a JVM, and
never participates in a run. Qt stays out of pulse-data and pulse-beacon entirely — the dependency
never points that way.

**2. A viewer reads a run's recorded artifacts, not the engine's internals or its human logs.** So
the artifact format is the real interface, and it comes first. Each run writes a standardized
directory (see [`../pulse-output.md`](../pulse-output.md)):

```
$PULSE_OUTPUT/<tier>/<app>/<runId>/
    run.json        manifest: mode, window, provenance, status and counts
    events.jsonl    the event recording
    console.log     the run's human log
```

`tier` is `historical` for deterministic replays and `live` for real-time runs.

**3. A viewer reports a run's health honestly.** `run.json` carries the engine outcome
(`runStatus`) and the recording outcome (`recordingStatus`) as *separate* fields, because either can
fail while the other succeeds, plus counts that always balance
(`observed = events + overflow + serializationErrors + abandoned`). A viewer must surface a partial
or failed recording as such, never present it as complete.

## Shared contents

- `schema/`: the authoritative contracts (JSON Schema 2020-12). Currently
  `event-record.schema.json`, the record kinds `header` / `event` / `trailer`.
- `run_browser.py`: the run picker, shared by every viewer — choosing a run is not specific to any
  one of them. Lists what `list_runs()` finds under the output root and classifies each run's health
  (`ok` / `partial` / `broken` / `running` / `unknown`) from its manifest, keeping the engine outcome
  and the recording outcome separate. Run it directly (`python run_browser.py [--root PATH]`) for a
  console listing with no Qt needed.
- `reference/run_layout.py`: the Pulse output layout in one place — resolve `$PULSE_OUTPUT`, create
  and finalize run directories, read manifests, and `list_runs()` to enumerate every run under the
  root, newest first. The Java mirror of this lives in pulse-beacon as `RunLayout`; the two are kept
  byte-for-byte compatible.
- `reference/event_record.py`: the Python side of the recording contract — build, write, read and
  validate records, plus `make_sample()` to emit a demo recording so a viewer can be developed
  without a JVM.
- `py_environment.yml`: enrichment layer for the shared `pulse` conda env (same pattern as
  pulse-beacon), adding only the viewers' extra packages.
- `requirements.txt`: the same dependencies (`PySide6`, `jsonschema`) for a plain `pip` install.

## Install

```bash
conda env update -f py_environment.yml     # enrich the shared `pulse` env
# or
pip install -r requirements.txt
```

---

# Viewers

## Events Viewer (`event_viewer.py`)

Reads one run's event recording and shows the dispatch stream.

```bash
python event_viewer.py                     # browse $PULSE_OUTPUT and pick a run
python event_viewer.py --root PATH         # browse a specific output root
python event_viewer.py recording.jsonl     # open one recording directly
```

With no argument it opens the **run browser**, which is the application's hub rather than a one-shot
picker: it stays open, and each run you open gets **its own detached window**, so several runs can be
compared side by side. Closing a viewer leaves the browser and the other viewers alone, and closing
the browser leaves the viewers open; `Run browser...` in any viewer brings it back.

A run is chosen by what it is — app, time, health — rather than by remembering a path. The chosen
run's verdict travels into its window's header bar, where the engine outcome and the recording
outcome stay separate and a partial capture is labelled partial. A recording opened as a bare
`.jsonl` has no manifest, so its health comes from the trailer instead.

Only the two columns that carry meaning are coloured: the tier (grey for `historical`, blue for
`live`) and the health verdict. Both palettes follow the desktop theme.

The output root can be chosen in the browser itself (`Change output folder...`) and is remembered
between sessions, so nothing has to be configured before the first launch; a remembered choice takes
precedence over `$PULSE_OUTPUT`, and an explicit `--root` over both. `Open recording file...` opens a
single `events.jsonl` from anywhere.

It validates the recording (diagnostics to the console, run metadata in the header bar), shows the
events newest-first by dispatch `seq`, colours each data type, and opens the selected event's
payload in a detail tree. Filter by topic, key, type, a `seq` range, or free text; click a column
header to sort (sorting by `type` groups by type and orders by `seq` within). Bounded retention,
batched inserts, and lazy payload parsing are in place for the larger recordings of later phases.

There is no synthetic fallback: if a recording is needed without a JVM, generate one explicitly with
`python reference/event_record.py`, which writes into the platform's temp directory (pass a path to
choose your own).

### The recording contract

A recording is JSONL, one JSON object per line:

- one `header` line: run metadata (runId, operating mode, window, type-universe fingerprint and
  provider ids from `RunInfo`, source identity);
- then `event` lines, ordered by `seq` (the dispatch sequence, the authoritative order);
- then a `trailer` line: completion status and the accounting totals.

Each `event` carries `seq`, `observedAt`, `eventTime`, `topic`, `key`, `typeId`, and `payload` (the
datum's fields as a JSON object). `typeId` is flat so the viewer can filter and colour without
parsing the payload; unknown types stay fully inspectable as generic JSON.

### Producing a recording

**Python (no JVM), for viewer development:**

```bash
python reference/event_record.py [outfile.jsonl]   # defaults to the platform temp directory
```

**Java:** the recorder now ships in pulse-beacon as
`com.inventzia.pulse.beacon.core.gateway.recording.EventRecorderGateway`, driven by the launcher-side
`run` package, so a normal run writes its recording into the output layout with no extra wiring.
`reference/EventRecorderGateway.java` is the original draft, kept for reference only — pulse-beacon
is authoritative and the two have diverged.

Two limits remain while the recorder is a subscriber sink rather than an engine tap (Stage A):

- Beacon routing is **one subscriber per `(topic, key)`**, so the recorder cannot be registered on a
  route that already has a sink. Use it only on routes with no other consumer.
- Its `seq` is **recorder-local** (the order this gateway received events on its subscribed routes),
  not the engine's global dispatch sequence.

Both facts are written into `run.json`'s `recording` descriptor, so a reader never mistakes a
partial recording for the complete engine stream. The global sequence and all-routes capture arrive
with the engine event tap (Stage B).

### Why not the existing JsonlWriterGateway

`JsonlWriterGateway.onEvent` writes bare `codec.toJson(payload)` with no topic, no type
discriminator, and no time, and it serializes on the dispatch thread. Mixed event types cannot be
told apart from that file, and it does not meet the recorder performance contract. Its format is
left unchanged (backward compatible); recording is a new, separate writer.

### Next

`recording contract -> offline viewer -> run browser -> live file following`. Done through the run
browser; next is phase 3, live file following (tail a recording as it is written) with the reader
contract from viewer.md section 5.

---

## License

Dual-licensed: GNU Affero General Public License v3.0 (see [LICENSE-AGPL-3.0](LICENSE-AGPL-3.0)) or
a commercial license from Inventzia Science and Technology Ltd. (see
[LICENSE-COMMERCIAL.txt](LICENSE-COMMERCIAL.txt) and [COMMERCIAL.md](COMMERCIAL.md)).

Third-party components are recorded in [NOTICE](NOTICE). **Packaging note:** PySide6 is LGPL, and
those obligations attach only to *distributing* it — declaring it as a dependency (a wheel on PyPI, a
conda recipe) ships a name, not the library, so nothing is owed; bundling Qt's binaries into a frozen
build (PyInstaller and friends) does convey it, and then the licence texts, the notice, and the
recipient's right to relink against their own Qt all apply. Also prefer non-Qt charting (pyqtgraph,
matplotlib): some Qt modules, Qt Charts among them, are GPL-or-commercial rather than LGPL, and GPL
would reach our own code.

Contributions require a DCO sign-off (`git commit -s`); see [CLA.md](CLA.md). Security reports:
[SECURITY.md](SECURITY.md).
