# Pulse Events Viewer, a design suggestion

## Goal

A desktop tool that shows the events flowing through a Pulse run as a scrollable grid log: newest at
the top, scroll down to go back in time. Each row is one event with a small fixed set of columns
(observed time, event time, key, topic, data type, payload) where the payload expands into a
structured view. The grid sorts (dispatch order and event time at least), filters (topic, key, data
type, time or sequence range), and shades each data type with a unique light background so the eye
can group by type.

## Approach: record the dispatch stream, consume it in a separate process

Not the human logs (the `Reporter` lines are for people: `str(datum)`, no format contract). Not a
participating actor either (an actor subscribes per `(topic, keys)`, so "everything" means
enumerating every topic and key and still missing new ones, and it puts viewer code inside the run).
Instead, record the dispatch stream as structured data and read it from a separate viewer process.

Package the viewer separately as **`pulse-viewer`**, keeping Qt out of the engine's dependencies.
Render payloads as generic JSON, so inspecting a run needs neither the domain adapters nor a JVM, and
**unknown types stay inspectable**.

## 1. Define the recording contract first

The existing `JsonlWriterGateway` cannot supply this. It writes bare `codec.toJson(payload)` with no
topic, no type discriminator, and no time (`JsonlWriterGateway.java:129`), so mixed event types
cannot be told apart from the file alone. Leave that gateway's format unchanged (backward
compatible) and add a new recording writer that emits a **versioned envelope**, one JSON object per
dispatched event (JSONL):

- `v`          envelope version
- `runId`      identifies the run
- `seq`        per-run monotonic dispatch sequence (the primary order; see section 2)
- `observedAt` wall-clock instant the tap saw the event (this is a tap timestamp, not gateway
               receipt time)
- `eventTime`  `datum.getDatumTime()`
- `topic`      the topic name
- `key`        `datum.getDatumKey()`
- `typeId`     the datum `TYPE_ID`
- `payload`    tagged JSON via `DatumCodec.toTaggedJson`, so the type is self-describing

Emit a **run header** once (first line, or a sidecar file): `runId`, envelope version, operating
mode and time window, source identity, and the type-universe fingerprint plus provider ids from
`RunInfo`. That lets the viewer show run metadata and flag a recording whose type universe does not
match the reader.

## 2. Order by dispatch sequence, not timestamps

Default sort is **descending `seq`**. Event time is not a safe default: the real-time path dispatches
in FIFO arrival order, which is not event-time order, and equal event times cannot be distinguished
by any timestamp. `seq` is the exact order the actors saw. Keep event time and `observedAt` as
alternative sort keys the user can switch to.

## 3. The observer tap, and why non-participation is not zero cost

"Off the actor contract" and "never publishes" keeps the tap causally safe, but that is not the same
as harmless: serialization, allocation, disk I/O, and any exception on the dispatch thread can move
latency, distort live timing, or fail the run. The tap must have an explicit performance contract:

- **On the dispatch thread, do the minimum.** Assign `seq`, capture `observedAt` and the references,
  and enqueue onto a bounded queue. Never serialize or write to disk on the dispatch thread.
- **A background worker** drains the queue, serializes to tagged JSON, and writes.
- **Bounded queue with explicit overflow behaviour.** Default is best-effort: on overflow drop and
  increment a monotonic dropped-record counter that the viewer surfaces. Lossless recording (bounded
  and block) is a separate, acknowledged performance policy, not the default.
- **Never fail the run.** The tap catches all exceptions; a viewer or recorder fault is logged and
  the run continues.
- **Zero cost when nothing is attached.** No tap, no work, like `runInfo()`.

The tap is off the actor contract (parity safe, like `runInfo()`), non-participating, and
cross-language because tagged JSON is the wire form both runtimes already produce.

## 4. The viewer (PySide6), with limits and a benchmark

`QTableView` plus `QSortFilterProxyModel` is the right shape, but the costs are real: the proxy
re-sorts and re-filters as its source changes, so appending rows is not free. Design to bounds:

- **Model:** a `QAbstractTableModel` over the records; columns observed time, event time, key, topic,
  data type, payload summary.
- **Bounded retention:** a ring buffer of the last N records (or last T of run time) in the model;
  the full record stays on disk. Prevents unbounded memory and keeps sort/filter tractable.
- **Batched UI updates:** coalesce arriving records and insert in chunks (one `beginInsertRows` per
  batch, on a short timer), not per record.
- **Lazy payload:** keep the raw JSON string per row and parse it into a `QTreeView` detail pane only
  when a row is selected or expanded.
- **Stable selection and scroll:** identify a selected row by `(runId, seq)` and preserve it, and do
  not jump the viewport, as records arrive.
- **Newest first:** default proxy sort descending on `seq`, so new records land on top and scrolling
  down goes back in time.
- **Two-criteria sort** via a custom proxy `lessThan` (for example data type, then `seq`), plus
  click-to-sort.
- **Filters** on topic, key, data type, and a time or sequence range (`filterAcceptsRow`); populate
  the choices from what has appeared.
- **Colour by data type:** hash `typeId` to a stable pastel (high lightness, low saturation) as the
  background of the data type cell.
- **Benchmark workload:** define one (for example E events/sec, T total, D distinct types) and
  measure insert, sort, and filter latency against it rather than assuming Qt makes it free.

## 5. Live following needs a reader contract

Tailing a file the recorder is still writing must handle:

- **Incomplete trailing line:** buffer a partial last record until its newline before parsing.
- **Truncation and rotation:** if the file shrinks or its inode changes, re-open by path. A
  `QFileSystemWatcher` stops watching a renamed or removed file, so poll and re-establish the watch.
- **Malformed records:** skip and count them, never fatal.
- **Flush latency:** the existing writer flushes every 100 events, which stalls quiet streams. The
  recording writer should flush per record or on a short timer for live viewing (configurable).
- **Pause-follow / resume-follow** controls, and visible dropped-record and skipped-line counts.

## Product direction and v1 scope

Package as `pulse-viewer` with Qt isolated from the engine; generic JSON payloads, no adapters or JVM
required, unknown types inspectable. This makes Pulse substantially easier to understand and debug,
and demonstrates its domain-independent nature.

Prioritise, for v1:

1. Offline recording inspection and payload details.
2. Topic / key / type filters.
3. Stable selection and scrolling as records arrive.
4. Pause-follow, resume-follow, and visible dropped-record counts.
5. Run metadata, including the schema fingerprint.

Keep ZMQ and remote access deferred.

## Recommended sequence

recording contract -> offline viewer -> live file following -> observer integration.

## Open questions

- Overflow default (drop-oldest vs drop-newest) and how prominently to surface drops.
- Retention sizing (N records vs T of run time) and whether the on-disk recording rotates.
- Run header as the first JSONL line vs a sidecar file.
- Whether `observedAt` is worth recording at all in compressed-time replay, where only `seq` and
  `eventTime` are meaningful.
</content>
