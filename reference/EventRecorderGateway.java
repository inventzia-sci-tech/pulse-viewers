/*
 * SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
 * Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
 *
 * DRAFT reference (pulse-viewer, recording-contract phase). Written for the
 * com.inventzia.pulse.beacon.core.gateway.recording package so it can be dropped into
 * pulse-beacon when the recording contract is adopted. Not yet part of any released package.
 */
package com.inventzia.pulse.beacon.core.gateway.recording;

import com.inventzia.pulse.beacon.core.AbstractGateway;
import com.inventzia.pulse.beacon.core.GatewayStatus;
import com.inventzia.pulse.beacon.core.Topic;
import com.inventzia.pulse.data.datum.Datum;
import com.inventzia.pulse.data.datum.DatumCodec;
import com.inventzia.pulse.data.datum.DatumTypeRegistry;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A sink gateway that records each dispatched event as a versioned JSONL envelope
 * (see pulse-viewer/schema/event-record.schema.json), for the Pulse Events Viewer.
 *
 * <p><b>Demo-only for now.</b> This is a <em>subscriber</em> gateway, and Beacon routing is
 * one-to-one per {@code (topic, key)}, so it cannot be registered on a route that already has a
 * sink (registration throws). Use it on controlled runs where the routes you want to record have no
 * other subscriber, until the engine observer hook exists. Its {@code seq} is therefore
 * <em>recorder-local</em> (the order this gateway received events on its subscribed routes), not the
 * engine's global dispatch sequence; the global sequence comes with the observer hook.
 *
 * <p>Performance contract: non-participation does not mean zero cost. {@link #onEvent} runs on the
 * dispatch thread and does the minimum only (count, stamp {@code seq}, capture {@code observedAt}
 * and the immutable datum reference, offer to a bounded queue). Serialization and disk I/O run on
 * this gateway's own thread ({@link #run()}). Overflow drops with a counter; a serialization failure
 * is counted and skipped; a disk failure fails the recorder without failing the run. All records are
 * accounted for in the trailer, so a failed recording never looks complete and lossless.
 */
public final class EventRecorderGateway extends AbstractGateway {

    private static final int ENVELOPE_VERSION = 1;

    private final Path       filePath;
    private final String     runId;
    private final String     recorderName;
    private final DatumCodec codec = DatumCodec.instance();

    private final BlockingQueue<Rec> queue;
    private final AtomicLong seq                 = new AtomicLong();
    private final AtomicLong observed            = new AtomicLong(); // every event the tap saw
    private final AtomicLong overflow            = new AtomicLong(); // queue full, never enqueued
    private final AtomicLong serializationErrors = new AtomicLong(); // dequeued but could not serialize

    private volatile boolean stopping    = false;
    private volatile boolean failed      = false;
    private volatile boolean interrupted = false;
    private BufferedWriter   writer;
    private long             written = 0; // writer-thread only

    /** Immutable per-event holder captured on the dispatch thread; serialized on the writer thread. */
    private record Rec(long seq, long observedAtMs, long eventTime, String topic, String key, Datum payload) {}

    /**
     * @param name      gateway instance name
     * @param filePath  JSONL output file (overwritten on each run)
     * @param runId     run identifier stamped on every record
     * @param startTime epoch millis, start of this gateway's window
     * @param endTime   epoch millis, end of this gateway's window
     * @param capacity  bounded observer-queue size; overflow drops (best-effort)
     */
    public EventRecorderGateway(String name, Path filePath, String runId,
                                long startTime, long endTime, int capacity) {
        super(name, startTime, endTime);
        this.filePath     = Objects.requireNonNull(filePath, "filePath");
        this.runId        = Objects.requireNonNull(runId, "runId");
        this.recorderName = "pulse-viewer/EventRecorderGateway v" + ENVELOPE_VERSION;
        this.queue        = new ArrayBlockingQueue<>(Math.max(1, capacity));
        setDriveClock(false);
    }

    /** True if the recorder aborted (e.g. a disk failure); the recording is then marked failed. */
    public boolean failed() {
        return failed;
    }

    // ------------------------------------------------------------------
    // Runnable — owns serialization + disk I/O; setup, loop, and each cleanup step are guarded
    // ------------------------------------------------------------------

    @Override
    public void run() {
        try {
            initialize();      // determines operatingMode() for the header
            openFile();
            writeHeader();
            connect();
            setStatus(GatewayStatus.STARTED);
            drain();
        } catch (InterruptedException e) {
            interrupted = true;
            Thread.currentThread().interrupt();
        } catch (Throwable t) {
            failed = true;                 // explicit failed state; onEvent stops accepting
            log.severe(name() + ": recorder failed, recording marked failed: " + t);
        } finally {
            stopping = true;               // ensure onEvent no longer enqueues
            flushBeforeTrailer();          // a failed flush of buffered events marks the recording failed
            safeWriteTrailer();            // status now reflects a late flush failure
            safeClose();                   // flush and close attempted independently
            setStatus(GatewayStatus.STOPPED);
        }
    }

    private void drain() throws InterruptedException {
        while (true) {
            Rec r = queue.poll(200, TimeUnit.MILLISECONDS);
            if (r != null) {
                writeEvent(r);             // disk failure here propagates -> run() marks failed
            } else {
                flushOrThrow();            // timely flush; a flush failure fails the recorder
                if (stopping && queue.isEmpty()) return;
            }
        }
    }

    // ------------------------------------------------------------------
    // Sub — dispatch thread: minimal work, never throws, never blocks
    // ------------------------------------------------------------------

    @Override
    public <P extends Datum> void onEvent(Topic<P> topic, P payload) {
        observed.incrementAndGet();        // total observed, whatever happens next
        if (stopping || failed) {
            return;                        // stopped or failed: reject (shows up as 'abandoned')
        }
        try {
            long s = seq.getAndIncrement();
            Rec r = new Rec(s, System.currentTimeMillis(), payload.getDatumTime(),
                            topic.name(), payload.getDatumKey(), payload);
            if (!queue.offer(r)) {
                overflow.incrementAndGet();
            }
        } catch (RuntimeException ex) {
            log.severe(name() + ": recorder rejected an event: " + ex);
        }
    }

    @Override
    public <P extends Datum> void publish(Topic<P> topic, P payload) {
        throw new UnsupportedOperationException(name() + ": EventRecorderGateway does not publish");
    }

    @Override
    public synchronized void disconnect() {
        super.disconnect();
        stopping = true;                   // run() drains the queue then exits
    }

    // ------------------------------------------------------------------
    // Serialization (writer thread only)
    // ------------------------------------------------------------------

    private void writeHeader() {
        DatumTypeRegistry reg = DatumTypeRegistry.defaultRegistry();
        String fp = reg.fingerprint();
        List<String> providers = reg.providers().stream()
                .map(DatumTypeRegistry.ProviderInfo::providerId).sorted().toList();
        StringBuilder b = new StringBuilder(256);
        b.append("{\"kind\":\"header\",\"v\":").append(ENVELOPE_VERSION)
         .append(",\"runId\":").append(str(runId))
         .append(",\"recorder\":").append(str(recorderName))
         .append(",\"recordedAt\":").append(str(Instant.now().toString()))
         .append(",\"operatingMode\":").append(str(operatingMode().name()))
         .append(",\"window\":{\"start\":").append(startTime()).append(",\"end\":").append(endTime()).append('}')
         .append(",\"typeFingerprint\":").append(fp == null ? "null" : str(fp))
         .append(",\"providerIds\":[");
        for (int i = 0; i < providers.size(); i++) {
            if (i > 0) b.append(',');
            b.append(str(providers.get(i)));
        }
        b.append("]}");
        writeLine(b.toString());
    }

    private void writeEvent(Rec r) {
        String line;
        try {                              // serialization failure: count, skip, keep recording
            String typeId;
            try {
                typeId = DatumTypeRegistry.defaultRegistry().typeIdOf(r.payload());
            } catch (RuntimeException e) {
                typeId = r.payload().getClass().getName(); // unregistered: still inspectable
            }
            String body = codec.toJson(r.payload());
            line = "{\"kind\":\"event\",\"v\":" + ENVELOPE_VERSION
                    + ",\"runId\":" + str(runId)
                    + ",\"seq\":" + r.seq()
                    + ",\"observedAt\":" + str(Instant.ofEpochMilli(r.observedAtMs()).toString())
                    + ",\"eventTime\":" + r.eventTime()
                    + ",\"topic\":" + str(r.topic())
                    + ",\"key\":" + str(r.key())
                    + ",\"typeId\":" + str(typeId)
                    + ",\"payload\":" + body + "}";
        } catch (RuntimeException serEx) {
            serializationErrors.incrementAndGet();
            log.severe(name() + ": failed to serialize event seq=" + r.seq() + ": " + serEx);
            return;
        }
        writeLine(line);                   // disk failure: UncheckedIOException -> fatal (propagates)
        written++;
    }

    /** Guarded so a trailer failure (e.g. the disk that just died) cannot prevent {@link #safeClose()}. */
    private void safeWriteTrailer() {
        try {
            if (writer == null) return;
            long obs = observed.get(), ovf = overflow.get(), ser = serializationErrors.get();
            long abandoned = Math.max(0, obs - written - ovf - ser);
            String status = failed ? "failed" : interrupted ? "interrupted" : "complete";
            writeLine("{\"kind\":\"trailer\",\"v\":" + ENVELOPE_VERSION
                    + ",\"runId\":" + str(runId)
                    + ",\"status\":" + str(status)
                    + ",\"observed\":" + obs
                    + ",\"events\":" + written
                    + ",\"overflow\":" + ovf
                    + ",\"serializationErrors\":" + ser
                    + ",\"abandoned\":" + abandoned + "}");
        } catch (Throwable t) {
            log.severe(name() + ": failed to write recording trailer: " + t);
        }
    }

    private void safeClose() {
        if (writer == null) return;
        try {
            writer.flush();
        } catch (IOException e) {           // a failed flush is a recorder failure, but must not skip close
            failed = true;
            log.severe(name() + ": flush on close failed, recording marked failed: " + e);
        }
        try {
            writer.close();
        } catch (IOException e) {
            log.severe(name() + ": failed to close recording file " + filePath + ": " + e);
        } finally {
            writer = null;
        }
    }

    // ------------------------------------------------------------------
    // File + JSON helpers
    // ------------------------------------------------------------------

    private void openFile() {
        try {
            writer = Files.newBufferedWriter(filePath,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to open recording file: " + filePath, e);
        }
    }

    private void writeLine(String line) {
        if (writer == null) return;
        try {
            writer.write(line);
            writer.newLine();
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to write recording: " + filePath, e);
        }
    }

    private void flushOrThrow() {
        if (writer == null) return;
        try {
            writer.flush();
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to flush recording: " + filePath, e);
        }
    }

    private void flushBeforeTrailer() {
        if (writer == null) return;
        try {
            writer.flush();
        } catch (IOException e) {
            failed = true;
            log.severe(name() + ": flush before trailer failed, recording marked failed: " + e);
        }
    }

    /** Minimal JSON string encoder (quotes + escapes), so this stays free of a JSON library. */
    private static String str(String s) {
        StringBuilder b = new StringBuilder(s.length() + 2).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"'  -> b.append("\\\"");
                case '\\' -> b.append("\\\\");
                case '\n' -> b.append("\\n");
                case '\r' -> b.append("\\r");
                case '\t' -> b.append("\\t");
                default -> {
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
                }
            }
        }
        return b.append('"').toString();
    }
}
