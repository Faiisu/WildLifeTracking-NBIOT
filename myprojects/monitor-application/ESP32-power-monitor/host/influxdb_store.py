"""InfluxDB 3 Core HTTP storage for ESP32 power measurements."""
from datetime import datetime, timezone
import json
import math
from queue import Empty, Full, Queue
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TABLE = "measurements"
VALID_RANGES = {60, 300, 900, 3600}


def _escape_string(value):
    value = str(value).replace("\r", " ").replace("\n", " ")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def line_protocol(frame, timestamp_ns):
    fields = [f"uptime_ms={frame['uptime_ms']}i"]
    for index in (0, 1):
        channel = frame.get(f"channel{index}")
        if isinstance(channel, dict):
            for key in ("battery_v", "load_v", "current_ma", "power_w"):
                value = channel.get(key)
                if type(value) not in (int, float) or not math.isfinite(value):
                    continue
                fields.append(f"channel{index}_{key}={float(value)!r}")
        error = frame.get(f"error{index}")
        if error:
            fields.append(f"error{index}={_escape_string(error)}")
    return f"{TABLE},device=esp32 " + ",".join(fields) + f" {timestamp_ns}"


def _influx_timestamp(value):
    if type(value) in (int, float):
        seconds, nanoseconds = divmod(int(value), 1_000_000_000)
        instant = datetime.fromtimestamp(seconds, timezone.utc)
        return f"{instant:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z"

    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    else:
        instant = instant.astimezone(timezone.utc)
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def decode_row(row):
    timestamp = _influx_timestamp(row["time"])
    frame = {"timestamp": timestamp, "received_at": timestamp,
             "uptime_ms": row.get("uptime_ms")}
    for index in (0, 1):
        channel = {}
        for key in ("battery_v", "load_v", "current_ma", "power_w"):
            value = row.get(f"channel{index}_{key}")
            if isinstance(value, (int, float)) and math.isfinite(value):
                channel[key] = value
        frame[f"channel{index}"] = channel or None
        error = row.get(f"error{index}")
        if error:
            frame[f"error{index}"] = error
    return frame


class InfluxDBStore:
    def __init__(self, base_url, database="esp32_power_monitor", timeout=5):
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.timeout = timeout

    def _request(self, path, body, content_type, method="POST"):
        request = Request(self.base_url + path, data=body, method=method,
                          headers={"Content-Type": content_type,
                                   "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            detail = error.read().decode("utf-8", "replace") if isinstance(error, HTTPError) else str(error)
            raise OSError(f"InfluxDB request failed: {detail}") from error

    def write_frames(self, frames):
        if not frames:
            return
        payload = "\n".join(line_protocol(frame, timestamp_ns)
                             for frame, timestamp_ns in frames).encode("utf-8")
        query = urlencode({"db": self.database, "precision": "nanosecond"})
        self._request(f"/api/v3/write_lp?{query}", payload, "text/plain; charset=utf-8")

    def clear_all(self):
        # Verify the server before using the destructive configuration API.
        try:
            info = json.loads(self._request("/ping", None, "application/json", method="GET"))
            version = tuple(int(part) for part in info["version"].split(".")[:2])
        except (ValueError, KeyError, TypeError) as error:
            raise OSError("Cannot verify InfluxDB version; deletion refused") from error
        if info.get("product_name") != "InfluxDB 3 Core" or version < (3, 12):
            raise OSError("Clearing data requires InfluxDB 3 Core 3.12 or newer")
        query = urlencode({"db": self.database, "hard_delete_at": "now"})
        self._request(f"/api/v3/configure/database?{query}", None,
                      "application/json", method="DELETE")
        try:
            self._request("/api/v3/configure/database",
                          json.dumps({"db": self.database}).encode(), "application/json")
            fields = [{"name": "uptime_ms", "type": "int64"}]
            for index in (0, 1):
                fields.extend({"name": f"channel{index}_{key}", "type": "float64"}
                              for key in ("battery_v", "load_v", "current_ma", "power_w"))
                fields.append({"name": f"error{index}", "type": "utf8"})
            self._request("/api/v3/configure/table", json.dumps({
                "db": self.database, "table": TABLE, "tags": ["device"],
                "fields": fields}).encode(), "application/json")
        except OSError as error:
            failure = OSError(f"Data was deleted, but database recreation failed: {error}")
            failure.data_deleted = True
            raise failure from error

    def query(self, sql):
        body = json.dumps({"db": self.database, "q": sql, "format": "jsonl"}).encode()
        result = self._request("/api/v3/query_sql", body, "application/json")
        rows = []
        for line in result.decode("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def latest(self):
        rows = self.query(f"SELECT * FROM {TABLE} ORDER BY time DESC LIMIT 1")
        return decode_row(rows[0]) if rows else None

    def history(self, seconds):
        if seconds not in VALID_RANGES:
            raise ValueError("Unsupported history range")
        # 4 Hz for one hour is 14,400 points; allow headroom for bursty timestamps.
        sql = (f"SELECT * FROM {TABLE} WHERE time >= now() - INTERVAL '{seconds} seconds' "
               "ORDER BY time ASC LIMIT 20000")
        return [decode_row(row) for row in self.query(sql)]


class InfluxDBWriter:
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
            try:
                self.store.clear_all()
            except OSError as error:
                if getattr(error, "data_deleted", False):
                    self.cleared_before_ns = time.time_ns()
                raise
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
        self.on_error(f"InfluxDB write failed after 3 attempts for {len(batch)} frames; JSONL backup retained")

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
