from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "host"))
from sqlite_store import SQLiteStore, SQLiteWriter, decode_row


FRAME = {
    "uptime_ms": 1250,
    "channel0": {
        "battery_v": 4.1,
        "load_v": 4.0,
        "current_ma": 25.0,
        "power_w": 0.1,
    },
    "channel1": None,
    "error1": 'sensor "offline"',
}


class SQLiteStoreTests(unittest.TestCase):
    def test_write_latest_and_history_between_preserve_precision_and_errors(self):
        store = SQLiteStore(":memory:")
        try:
            self.assertIsNone(store.latest())
            ts1 = 1_760_000_001_123_456_789
            ts2 = 1_760_000_002_000_000_000
            frame2 = {
                "uptime_ms": 1500,
                "channel0": None,
                "error0": "read failed",
                "channel1": {
                    "battery_v": 3.9,
                    "load_v": 3.8,
                    "current_ma": 10.5,
                    "power_w": 0.04,
                },
            }
            store.write_frames([(FRAME, ts1), (frame2, ts2)])

            latest = store.latest()
            self.assertEqual(latest["uptime_ms"], 1500)
            self.assertIsNone(latest["channel0"])
            self.assertEqual(latest["error0"], "read failed")
            self.assertEqual(latest["channel1"], frame2["channel1"])

            rows = store.history_between(1_760_000_001_000, 1_760_000_002_000)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["uptime_ms"], 1250)
            self.assertEqual(rows[0]["channel0"], FRAME["channel0"])
            self.assertIsNone(rows[0]["channel1"])
            self.assertEqual(rows[0]["error1"], 'sensor "offline"')
            self.assertTrue(rows[0]["timestamp"].endswith(".123456789Z"))
            self.assertEqual(rows[0]["received_at"], rows[0]["timestamp"])
        finally:
            store.close()

    def test_history_range_filtering_and_validation(self):
        store = SQLiteStore(":memory:")
        try:
            now_ns = time.time_ns()
            old_ns = now_ns - 120 * 1_000_000_000
            recent_ns = now_ns - 10 * 1_000_000_000
            store.write_frames([(FRAME, old_ns), ({**FRAME, "uptime_ms": 2000}, recent_ns)])

            rows = store.history(60)
            self.assertEqual([r["uptime_ms"] for r in rows], [2000])
            with self.assertRaises(ValueError):
                store.history(42)
        finally:
            store.close()

    def test_annotations_overlap_query_and_deletion_preserve_measurements(self):
        store = SQLiteStore(":memory:")
        try:
            store.write_frames([(FRAME, 1_500 * 1_000_000)])
            gps = store.add_annotation(1000, 3000, 'GPS "finding"')
            lte = store.add_annotation(2500, 4500, "LTE init")

            overlapping = store.annotations_between(2000, 3500)
            self.assertEqual([a["id"] for a in overlapping], [gps["id"], lte["id"]])
            self.assertEqual(overlapping[0]["text"], 'GPS "finding"')

            store.delete_annotation(gps["id"])
            remaining = store.annotations_between(2000, 3500)
            self.assertEqual([a["id"] for a in remaining], [lte["id"]])
            self.assertEqual(len(store.history_between(1000, 2000)), 1)

            with self.assertRaises(KeyError):
                store.delete_annotation(gps["id"])

            for bad_id in ["bad", "'; DELETE FROM measurements", "A" * 32]:
                with self.assertRaises(ValueError):
                    store.delete_annotation(bad_id)
        finally:
            store.close()

    def test_file_persistence_and_clear_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nested" / "monitor.db"
            store = SQLiteStore(db_path)
            try:
                store.write_frames([(FRAME, 1_760_000_000_000_000_000)])
                label = store.add_annotation(1000, 2000, "MQTT upload")
            finally:
                store.close()

            reopened = SQLiteStore(db_path)
            try:
                self.assertEqual(reopened.latest()["uptime_ms"], 1250)
                self.assertEqual(
                    [a["id"] for a in reopened.annotations_between(1000, 2000)],
                    [label["id"]],
                )
                reopened.clear_all()
                self.assertIsNone(reopened.latest())
                self.assertEqual(reopened.annotations_between(1000, 2000), [])

                reopened.write_frames([({**FRAME, "uptime_ms": 3000}, 1_760_000_005_000_000_000)])
                self.assertEqual(reopened.latest()["uptime_ms"], 3000)
            finally:
                reopened.close()

    def test_decode_row_restores_nanosecond_timestamp_and_channels(self):
        point = decode_row(
            {
                "time_ns": 1_791_799_200_123_456_789,
                "uptime_ms": 1250,
                "channel0_battery_v": None,
                "channel0_load_v": 4.0,
                "channel0_current_ma": None,
                "channel0_power_w": 0.1,
                "error0": None,
                "channel1_battery_v": None,
                "channel1_load_v": None,
                "channel1_current_ma": None,
                "channel1_power_w": None,
                "error1": "offline",
            }
        )
        self.assertEqual(point["timestamp"], "2026-10-12T10:00:00.123456789Z")
        self.assertEqual(point["received_at"], "2026-10-12T10:00:00.123456789Z")
        self.assertEqual(point["channel0"], {"load_v": 4.0, "power_w": 0.1})
        self.assertIsNone(point["channel1"])
        self.assertEqual(point["error1"], "offline")

    def test_closed_store_raises_oserror(self):
        store = SQLiteStore(":memory:")
        store.close()
        with self.assertRaises(OSError):
            store.latest()

    def test_writer_discards_old_batch_and_writes_new_readings_after_clear(self):
        store = Mock()
        writer = SQLiteWriter(store, Mock(), batch_interval=0.01)
        try:
            with patch("sqlite_store.time.time_ns", return_value=100):
                writer.clear_all()
            writer._flush([(FRAME, 99), (FRAME, 101)])
            store.clear_all.assert_called_once()
            store.write_frames.assert_called_once_with([(FRAME, 101)])
        finally:
            writer.close()

    def test_failed_clear_does_not_discard_pending_readings(self):
        store = Mock()
        store.clear_all.side_effect = OSError("locked")
        writer = SQLiteWriter(store, Mock(), batch_interval=0.01)
        try:
            with self.assertRaises(OSError):
                writer.clear_all()
            writer._flush([(FRAME, 99)])
            store.write_frames.assert_called_once_with([(FRAME, 99)])
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
