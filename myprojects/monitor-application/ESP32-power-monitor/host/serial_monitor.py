"""Run the local firmware through mpremote and record measurement JSON lines."""
import argparse
from contextlib import nullcontext
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time

from sqlite_store import SQLiteStore, SQLiteWriter, VALID_RANGES


APP_DIR = Path(__file__).resolve().parent.parent


def parse_frame(line):
    try:
        frame = json.loads(line)
    except ValueError:
        return None
    if not isinstance(frame, dict) or type(frame.get("uptime_ms")) is not int or frame["uptime_ms"] < 0:
        return None
    for channel in ("channel0", "channel1"):
        if channel not in frame:
            return None
        values = frame[channel]
        if values is None:
            continue
        if not isinstance(values, dict):
            return None
        for name in ("battery_v", "load_v", "current_ma", "power_w"):
            value = values.get(name)
            if type(value) not in (int, float) or not math.isfinite(value):
                return None
    return frame


def forward_line(line, log, output=None, diagnostics=None, on_frame=None):
    output = sys.stdout if output is None else output
    diagnostics = sys.stderr if diagnostics is None else diagnostics
    line = line.strip()
    if not line:
        return False
    frame = parse_frame(line)
    if frame is None:
        print(line, file=diagnostics, flush=True)
        return False
    timestamp_ns = time.time_ns()
    frame["received_at"] = datetime.fromtimestamp(
        timestamp_ns / 1_000_000_000, timezone.utc).isoformat()
    encoded = json.dumps(frame, allow_nan=False, separators=(",", ":"))
    log.write(encoded + "\n")
    log.flush()
    print(encoded, file=output, flush=True)
    if on_frame is not None:
        on_frame(frame, timestamp_ns)
    return True


def stop_process(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class MonitorState:
    def __init__(self, port, log_path, store=None, writer=None):
        self.lock = threading.Lock()
        self.port = port
        self.log_path = str(log_path)
        self.status = "connecting"
        self.error = ""
        self.latest = None
        self.count = 0
        self.history = deque(maxlen=3600)
        self.raw_count = 0
        self.raw = deque(maxlen=200)
        self.store = store
        self.writer = writer
        self.database_error = ""

    def ingest(self, line, log):
        line = line.strip()
        if not line:
            return
        accepted = {}
        def save_frame(frame, timestamp_ns):
            accepted["frame"] = frame
            accepted["timestamp_ns"] = timestamp_ns
            if self.writer is not None:
                try:
                    self.writer.submit(frame, timestamp_ns)
                except queue.Full:
                    self.set_database_error("SQLite write queue is full; sample retained in JSONL only")
        valid = forward_line(line, log, on_frame=save_frame)
        with self.lock:
            self.raw_count += 1
            self.raw.append({"id": self.raw_count, "text": line})
            if valid:
                self.latest = accepted["frame"]
                self.count += 1
                self.history.append({"id": self.count, "received_at": self.latest["received_at"],
                                     "channel0": self.latest["channel0"],
                                     "channel1": self.latest["channel1"]})
                self.status = "reading"

    def set_database_error(self, message):
        with self.lock:
            self.database_error = message

    def snapshot(self):
        with self.lock:
            result = {"port": self.port, "log_path": self.log_path, "status": self.status,
                      "error": self.error, "latest": self.latest, "count": self.count,
                      "raw_count": self.raw_count, "raw": list(self.raw),
                      "database_error": self.database_error}
        if self.store is not None:
            result["latest"] = self.store.latest()
        return result

    def history_data(self, seconds):
        if seconds not in VALID_RANGES:
            raise ValueError("Unsupported history range")
        if self.store is not None:
            return self.store.history(seconds)
        with self.lock:
            return [{key: value for key, value in sample.items() if key != "id"}
                    for sample in list(self.history)[-seconds * 4:]]

    def clear_database(self):
        if self.store is None:
            raise OSError("Database is not configured")
        if self.writer is not None:
            self.writer.clear_all()
        else:
            self.store.clear_all()
        with self.lock:
            self.latest = None
            self.history.clear()
            self.database_error = ""

    def finish(self, code, error=""):
        with self.lock:
            self.status = "stopped" if code == 0 else "error"
            self.error = error or ("mpremote exited with code " + str(code) if code else "")

    def history_snapshot(self, after=None, limit=3600):
        with self.lock:
            samples = list(self.history)
            oldest = samples[0]["id"] if samples else self.count + 1
            truncated = after is not None and after < oldest - 1
            if after is None or truncated:
                samples = samples[-limit:]
            else:
                samples = [sample for sample in samples if sample["id"] > after][:limit]
            return {"samples": samples, "oldest_id": oldest, "latest_id": self.count,
                    "truncated": truncated}


def validate_interval(start_ms, end_ms):
    if (type(start_ms) is not int or type(end_ms) is not int or start_ms < 0
            or end_ms <= start_ms or end_ms > 9_223_372_036_854
            or end_ms - start_ms > 86_400_000):
        raise ValueError("Choose an increasing time range of at most 24 hours")
    return start_ms, end_ms


def create_server(state, port):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")

        def end_headers(self):
            self._cors()
            super().end_headers()

        def do_OPTIONS(self):
            from urllib.parse import urlsplit
            origin = urlsplit(self.headers.get("Origin", ""))
            if origin.hostname not in ("127.0.0.1", "localhost", "::1"):
                self.send_error(403, "Local frontend required")
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Confirm-Clear, X-Confirm-Label")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_DELETE(self):
            from urllib.parse import urlsplit
            label_path = self.path.startswith("/api/annotations/")
            if self.path != "/api/data" and not label_path:
                self.send_error(404)
                return
            origin = urlsplit(self.headers.get("Origin", ""))
            if (self.headers.get("Origin") and
                    origin.hostname not in ("127.0.0.1", "localhost", "::1")):
                self.send_error(403, "Local frontend required")
                return
            if label_path:
                if self.headers.get("X-Confirm-Label") != "delete":
                    self.send_error(400, "Explicit label deletion confirmation required")
                    return
                try:
                    if state.store is None:
                        raise OSError("Database is not configured")
                    with state.writer.write_lock if state.writer else nullcontext():
                        state.store.delete_annotation(self.path[len("/api/annotations/"):])
                except ValueError as error:
                    self.send_error(400, str(error))
                    return
                except KeyError:
                    self.send_error(404, "Label not found")
                    return
                except OSError as error:
                    self.send_error(503, str(error))
                    return
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if self.headers.get("X-Confirm-Clear") != "delete-all":
                self.send_error(400, "Explicit deletion confirmation required")
                return
            try:
                state.clear_database()
            except OSError as error:
                self.send_error(503, str(error))
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_POST(self):
            from urllib.parse import urlsplit
            if self.path != "/api/annotations":
                self.send_error(404)
                return
            origin = urlsplit(self.headers.get("Origin", ""))
            if (self.headers.get("Origin") and
                    origin.hostname not in ("127.0.0.1", "localhost", "::1")):
                self.send_error(403, "Local frontend required")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected a JSON annotation of at most 4096 bytes")
                data = json.loads(self.rfile.read(length))
                start, end = validate_interval(data["start_ms"], data["end_ms"])
                text = data["text"]
                if not isinstance(text, str) or not text.strip() or len(text) > 200 or any(ord(c) < 32 for c in text):
                    raise ValueError("Label must contain 1 to 200 printable characters")
            except (ValueError, KeyError, TypeError) as error:
                self.send_error(400, str(error))
                return
            try:
                if state.store is None:
                    raise OSError("Database is not configured")
                with state.writer.write_lock if state.writer else nullcontext():
                    annotation = state.store.add_annotation(start, end, text.strip())
            except OSError as error:
                self.send_error(503, str(error))
                return
            body = json.dumps(annotation).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            parsed = urlsplit(self.path)
            if self.path == "/api/state":
                try:
                    body = json.dumps(state.snapshot(), allow_nan=False).encode()
                except OSError as error:
                    self.send_error(503, str(error))
                    return
                content_type = "application/json; charset=utf-8"
            elif parsed.path == "/api/history":
                query = parse_qs(parsed.query)
                try:
                    if "start_ms" in query or "end_ms" in query:
                        start, end = validate_interval(int(query["start_ms"][0]), int(query["end_ms"][0]))
                        if state.store is None:
                            raise OSError("Database is not configured")
                        samples = state.store.history_between(start, end)
                        annotations = state.store.annotations_between(start, end)
                    else:
                        seconds = int(query.get("seconds", [300])[0])
                        samples = state.history_data(seconds)
                        annotations = []
                    body = json.dumps({"samples": samples, "annotations": annotations}, allow_nan=False).encode()
                except (ValueError, KeyError, TypeError) as error:
                    self.send_error(400, str(error))
                    return
                except OSError as error:
                    self.send_error(503, str(error))
                    return
                content_type = "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--mpremote", required=True)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--api-port", type=int, default=3101)
    parser.add_argument("--db", type=Path, default=APP_DIR / "data" / "esp32_power_monitor.db")
    args = parser.parse_args()
    log_path = args.log or APP_DIR / "logs" / datetime.now().strftime("measurements-%Y%m%d-%H%M%S-%f.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(args.db)
    state = MonitorState(args.port, log_path, store)
    writer = SQLiteWriter(store, state.set_database_error)
    state.writer = writer
    command = [args.mpremote, "connect", args.port, "sleep", "1", "mount", str(APP_DIR), "exec",
               "import sys; sys.path.insert(0, '/remote'); import main"]
    process = None
    server = None
    worker = None
    interrupted = False
    exit_code = 0
    try:
        server = create_server(state, args.api_port)
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "w") as log:
            print("JSONL log: " + str(log_path), file=sys.stderr, flush=True)
            print("SQLite DB: " + str(args.db), file=sys.stderr, flush=True)
            print("Backend API: http://127.0.0.1:" + str(server.server_port), file=sys.stderr, flush=True)
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace", start_new_session=True,
                                       env={**os.environ, "PYTHONUNBUFFERED": "1"})

            def receive():
                try:
                    for line in process.stdout:
                        state.ingest(line, log)
                    state.finish(process.wait())
                except (OSError, ValueError) as error:
                    state.finish(1, str(error))
                    stop_process(process)

            worker = threading.Thread(target=receive, daemon=True)
            worker.start()
            try:
                # Keep the page available after a serial failure so its raw error can be read.
                server.serve_forever(poll_interval=0.2)
            finally:
                stop_process(process)
                worker.join(timeout=3)
    except KeyboardInterrupt:
        interrupted = True
    except OSError as error:
        print("Serial monitor error: " + str(error), file=sys.stderr)
        exit_code = 1
    finally:
        if server is not None:
            server.server_close()
        if process is not None:
            stop_process(process)
            if worker is not None:
                worker.join(timeout=3)
            process.stdout.close()
        writer.close()
        store.close()
        if interrupted and process is not None:
            # A host interrupt can leave the device executing its temporary loop.
            # Entering raw REPL interrupts it without resetting or writing device files.
            try:
                result = subprocess.run([args.mpremote, "connect", args.port, "resume", "exec", "pass"],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=5, text=True, errors="replace", start_new_session=True)
                if result.returncode:
                    print(result.stdout, file=sys.stderr)
                    print("Could not stop the device loop; press board Reset.", file=sys.stderr)
            except (OSError, subprocess.TimeoutExpired):
                print("Could not stop the device loop; press board Reset.", file=sys.stderr)
            print("Monitor stopped. Press board Reset to run its original code.", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
