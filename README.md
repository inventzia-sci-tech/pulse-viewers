# pulse-viewer (draft)

Working area for the Pulse Events Viewer. The design rationale is in
[`../viewer.md`](../viewer.md); this directory holds the first buildable artifact of its first
phase, **the recording contract**.

Nothing here is wired into pulse-data or pulse-beacon yet. It is a draft to review before landing
the recorder in the engine and starting the viewer.

## What "recording contract" means

The viewer reads a recording, not the engine's internals or its human logs. So the recording format
is the real interface, and it comes first. A recording is JSONL (one JSON object per line):

- one `header` line: run metadata (runId, operating mode, window, type-universe fingerprint and
  provider ids from `RunInfo`, source identity);
- then `event` lines, ordered by `seq` (the dispatch sequence, the authoritative order);
- then an optional `trailer` line: totals and dropped-record count.

Each `event` carries `seq`, `observedAt`, `eventTime`, `topic`, `key`, `typeId`, and `payload`
(the datum's fields as a JSON object). `typeId` is flat so the viewer can filter and colour without
parsing the payload; unknown types stay fully inspectable as generic JSON.

## Contents

- `schema/event-record.schema.json`: the authoritative contract (JSON Schema 2020-12), the record
  kinds `header` / `event` / `trailer`.
- `reference/EventRecorderGateway.java`: engine-side reference recorder. A sink gateway that
  follows the performance contract from viewer.md: the dispatch thread only assigns `seq`, captures
  `observedAt` and the immutable datum reference, and offers it to a bounded queue; serialization
  and disk I/O run on the gateway's own thread; overflow drops with a counter (best-effort);
  a recorder fault never fails the run. Written for the
  `com.inventzia.pulse.beacon.core.gateway.recording` package so it can move into pulse-beacon at
  the observer-integration step.
- `reference/event_record.py`: the Python side of the same contract: build/write/read/validate
  records, and `make_sample()` to emit a demo recording so the offline viewer can be built without a
  JVM.
- `viewer.py`: the phase-2 offline viewer (PySide6). Reads and validates a recording, then shows the
  events in a sortable/filterable grid with per-type colour and a payload detail tree.
- `py_environment.yml`: enrichment layer for the shared `pulse` conda env (same pattern as
  pulse-beacon), adding only the viewer's extra packages.
- `requirements.txt`: the same dependencies (`PySide6`, `jsonschema`) for a plain `pip` install.
  Deliberately separate from the engine, so Qt never enters pulse-data or pulse-beacon.

## Produce a recording

**Python (no JVM), for viewer development:**

```bash
python reference/event_record.py /tmp/pulse-sample.jsonl
```

**Java (controlled demos only):** register `EventRecorderGateway` as a subscriber on the routes you
want to record, start it on its own thread like any sink gateway, and run the engine. Two limits,
because it is a subscriber sink and not yet an engine tap:

- Beacon routing is **one subscriber per `(topic, key)`**, so the recorder cannot be registered on a
  route that already has a sink (registration throws). Use it only on routes with no other consumer,
  until the observer hook exists.
- Its `seq` is **recorder-local** (the order this gateway received events on its subscribed routes),
  not the engine's global dispatch sequence. The global sequence arrives with the observer hook.

The recorder still accounts for every event it saw: the trailer carries a completion `status` and
the counts `observed = events + overflow + serializationErrors + abandoned`, so a failed or lossy
recording never looks complete.

## Why not the existing JsonlWriterGateway

`JsonlWriterGateway.onEvent` writes bare `codec.toJson(payload)` with no topic, no type
discriminator, and no time, and it serializes on the dispatch thread. Mixed event types cannot be
told apart from that file, and it does not meet the recorder performance contract. Its format is
left unchanged (backward compatible); recording is a new, separate writer.

## Run the offline viewer (phase 2)

```bash
conda env update -f py_environment.yml   # enrich the shared `pulse` env (or: pip install -r requirements.txt)
python viewer.py [recording.jsonl]       # defaults to a generated /tmp/pulse-sample.jsonl
```

It validates the recording (diagnostics to the console, run metadata in the header bar), shows the
events newest-first by dispatch `seq`, colours each data type, and opens the selected event's payload
in a detail tree. Filter by topic, key, type, a `seq` range, or free text; click a column header to
sort (sorting by `type` groups by type and orders by `seq` within). Bounded retention, batched
inserts, and lazy payload parsing are in place for the larger recordings of later phases.

## Next

`recording contract -> offline viewer -> live file following -> observer integration`. Done through
the offline viewer; next is phase 3, live file following (tail a recording as it is written), with
the reader contract from viewer.md section 5.
</content>
