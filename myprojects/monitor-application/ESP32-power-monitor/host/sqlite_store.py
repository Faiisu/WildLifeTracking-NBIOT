"""Local SQLite storage for ESP32 power measurements and operation labels."""
from datetime import datetime, timezone
import math
from pathlib import Path
from queue import Empty, Queue
import re
import sqlite3
import threading
import time
import uuid


TABLE = "measurements"
VALID_RANGES = {60, 300, 900, 3600}
_CHANNEL_KEYS = ("battery_v", "load_v", "current_ma", "power_w")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS measurements (
    time_ns INTEGER NOT NULL,
    uptime_ms INTEGER NOT NULL,
    channel0_battery_v REAL,
    channel0_load_v REAL,
    channel0_current_ma REAL,
    channel0_power_w REAL,
    error0 TEXT,
    channel1_battery_v REAL,
    channel1_load_v REAL,
    channel1_current_ma REAL,
    channel1_power_w REAL,
    error1 TEXT
);
CREATE INDEX IF NOT EXISTS idx_measurements_time_ns ON measurements (time_ns);
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_range ON annotations (start_ms, end_ms);
"""

_INSERT_MEASUREMENT_SQL = """
INSERT INTO measurements (
    time_ns,
    uptime_ms,
    channel0_battery_v,
    channel0_load_v,
    channel0_current_ma,
    channel0_power_w,
    error0,
    channel1_battery_v,
    channel1_load_v,
    channel1_current_ma,
    channel1_power_w,
    error1
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _format_timestamp(timestamp_ns):
    seconds, nanoseconds = divmod(int(timestamp_ns), 1_000_000_000)
    instant = datetime.fromtimestamp(seconds, timezone.utc)
    return f"{instant:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z"


def _finite_float(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    return None


def _frame_to_row(frame, timestamp_ns):
    values = [int(timestamp_ns), int(frame["uptime_ms"])]
    for index in (0, 1):
        channel = frame.get(f"channel{index}")
        if isinstance(channel, dict):
            values.extend(_finite_float(channel.get(key)) for key in _CHANNEL_KEYS)
        else:
            values.extend((None, None, None, None))
        error = frame.get(f"error{index}")
        values.append(str(error) if error else None)
    return tuple(values)


def decode_row(row):
    timestamp = _format_timestamp(row["time_ns"])
    frame = {
        "timestamp": timestamp,
        "received_at": timestamp,
        "uptime_ms": row["uptime_ms"],
    }
    for index in (0, 1):
        channel = {}
        for key in _CHANNEL_KEYS:
            value = row[f"channel{index}_{key}"]
            if isinstance(value, (int, float)) and math.isfinite(value):
                channel[key] = value
        frame[f"channel{index}"] = channel or None
        error = row[f"error{index}"]
        if error:
            frame[f"error{index}"] = error
    return frame


class SQLiteStore:
    def __init__(self, db_path, timeout=5.0):
        self.db_path = str(db_path)
        self.timeout = timeout
        self._lock = threading.RLock()
        if self.db_path != ":memory:" and not self.db_path.startswith("file:"):
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
                self._conn.executescript(_SCHEMA_SQL)
        except sqlite3.Error as error:
            raise OSError(f"SQLite initialization failed: {error}") from error

    def close(self):
        with self._lock:
            self._conn.close()

    def write_frames(self, frames):
        if not frames:
            return
        rows = [_frame_to_row(frame, timestamp_ns) for frame, timestamp_ns in frames]
        try:
            with self._lock, self._conn:
                self._conn.executemany(_INSERT_MEASUREMENT_SQL, rows)
        except sqlite3.Error as error:
            raise OSError(f"SQLite write failed: {error}") from error

    def clear_all(self):
        try:
            with self._lock, self._conn:
                self._conn.execute(f"DELETE FROM {TABLE}")
                self._conn.execute("DELETE FROM annotations")
        except sqlite3.Error as error:
            raise OSError(f"SQLite clear failed: {error}") from error

    def latest(self):
        try:
            with self._lock:
                row = self._conn.execute(
                    f"SELECT * FROM {TABLE} ORDER BY time_ns DESC, rowid DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise OSError(f"SQLite query failed: {error}") from error
        return decode_row(row) if row is not None else None

    def history(self, seconds):
        if seconds not in VALID_RANGES:
            raise ValueError("Unsupported history range")
        cutoff_ns = time.time_ns() - seconds * 1_000_000_000
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM {TABLE} WHERE time_ns >= ? "
                    "ORDER BY time_ns ASC, rowid ASC LIMIT 20000",
                    (cutoff_ns,),
                ).fetchall()
        except sqlite3.Error as error:
            raise OSError(f"SQLite query failed: {error}") from error
        return [decode_row(row) for row in rows]

    def history_between(self, start_ms, end_ms):
        start_ns = start_ms * 1_000_000
        end_ns = end_ms * 1_000_000
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM {TABLE} WHERE time_ns >= ? AND time_ns <= ? "
                    "ORDER BY time_ns ASC, rowid ASC",
                    (start_ns, end_ns),
                ).fetchall()
        except sqlite3.Error as error:
            raise OSError(f"SQLite query failed: {error}") from error
        return [decode_row(row) for row in rows]

    def add_annotation(self, start_ms, end_ms, text):
        annotation = {
            "id": uuid.uuid4().hex,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
        }
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO annotations (id, start_ms, end_ms, text) VALUES (?, ?, ?, ?)",
                    (annotation["id"], start_ms, end_ms, text),
                )
        except sqlite3.Error as error:
            raise OSError(f"SQLite insert failed: {error}") from error
        return annotation

    def annotations_between(self, start_ms, end_ms):
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, start_ms, end_ms, text FROM annotations "
                    "WHERE start_ms <= ? AND end_ms >= ? ORDER BY start_ms ASC, id ASC",
                    (end_ms, start_ms),
                ).fetchall()
        except sqlite3.Error as error:
            raise OSError(f"SQLite query failed: {error}") from error
        return [
            {
                "id": row["id"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "text": row["text"],
            }
            for row in rows
        ]

    def delete_annotation(self, annotation_id):
        if not isinstance(annotation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", annotation_id):
            raise ValueError("Invalid label ID")
        try:
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM annotations WHERE id = ?",
                    (annotation_id,),
                )
        except sqlite3.Error as error:
            raise OSError(f"SQLite delete failed: {error}") from error
        if cursor.rowcount == 0:
            raise KeyError("Label not found")


class SQLiteWriter:
    """Batch writes off the serial reader so database I/O cannot pace USB reads."""

    def __init__(self, store, on_error, batch_interval=0.5, queue_size=3600):
        self.store = store
        self.on_error = on_error
        self.batch_interval = batch_interval
        self.queue = Queue(maxsize=queue_size)
        self.write_lock = threading.Lock()
        self.cleared_before_ns = 0
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, frame, timestamp_ns):
        self.queue.put_nowait((dict(frame), timestamp_ns))

    def close(self, timeout=5):
        self.stopping.set()
        self.thread.join(timeout)

    def clear_all(self):
        with self.write_lock:
            self.store.clear_all()
            # Discard pre-clear frames, including a batch already held by the worker.
            self.cleared_before_ns = time.time_ns()
            self.on_error("")

    def _flush(self, batch):
        with self.write_lock:
            batch = [item for item in batch if item[1] > self.cleared_before_ns]
            if batch:
                self._write_batch(batch)

    def _write_batch(self, batch):
        for attempt in range(3):
            try:
                self.store.write_frames(batch)
                self.on_error("")
                return
            except OSError as error:
                self.on_error(str(error))
                if attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
        self.on_error(
            f"SQLite write failed after 3 attempts for {len(batch)} frames; JSONL backup retained"
        )

    def _run(self):
        batch = []
        flush_at = time.monotonic() + self.batch_interval
        while not self.stopping.is_set() or batch or not self.queue.empty():
            timeout = max(0, flush_at - time.monotonic()) if batch else self.batch_interval
            try:
                batch.append(self.queue.get(timeout=timeout))
            except Empty:
                pass
            if batch and (len(batch) >= 4 or time.monotonic() >= flush_at or self.stopping.is_set()):
                while len(batch) < 100:
                    try:
                        batch.append(self.queue.get_nowait())
                    except Empty:
                        break
                self._flush(batch)
                batch = []
                flush_at = time.monotonic() + self.batch_interval
