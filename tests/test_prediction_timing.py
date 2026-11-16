import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.prediction_timing import (
    TIMING_VERSION, utc, validation_capture, update_journal, evaluate_entry, covers,
)
from scripts.validate_prediction_history import classify, main, load_history, save_history


def at(clock):
    return utc("2026-11-16T" + clock + "+00:00")


def event(identity, clock, received="11:05:00"):
    return {"id": str(identity), "timestamp_utc": at(clock).isoformat(),
            "received_at_utc": at(received).isoformat(), "regions": ["peninsula"],
            "band": "20m", "receiver": "EA2ABC"}


def prediction():
    return {"generated_at_utc": at("10:00:00").isoformat(), "evaluation": None,
            "validation_method_version": TIMING_VERSION,
            "recommendations": {"peninsula": {"first": "20 m", "alternative": "40m"}}}


def score(entry, journal, clock="13:00:00"):
    evaluate_entry(entry, journal, at(clock), ("peninsula",), ("20m", "40m"), classify)
    return (entry.get("evaluation") or {}).get("peninsula", {}).get("20m")


class TemporalValidationTests(unittest.TestCase):
    def test_collector_main_exports_timestamped_evidence(self):
        from scripts import collect_pskreporter_regions as collector
        stamp = at("10:30:00").timestamp()
        xml = ('<reports>' + ''.join(
            f'<receptionReport flowStartSeconds="{stamp}" frequency="14074000" senderCallsign="EA1A{i}" senderLocator="IN80" receiverCallsign="EA2ABC" receiverLocator="IN91" mode="FT8" />'
            for i in range(250)) + '</reports>').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ["collector", "--output", str(root / "psk.json"), "--history", str(root / "history.json"), "--diagnostic", str(root / "diag.json")]
            with patch("sys.argv", argv), patch.object(collector, "fetch_with_retries", return_value=(xml, "application/xml", 1)), patch.object(collector.time, "sleep"), patch.object(collector.time, "time", return_value=at("11:00:00").timestamp()), patch.object(collector, "now_iso", return_value=at("11:00:00").isoformat()):
                self.assertEqual(collector.main(), 0)
            output = json.loads((root / "psk.json").read_text(encoding="utf-8"))
            self.assertEqual(len(output["validation_capture"]["events"]), 250)
            self.assertEqual(len(output["regions"]["peninsula"]["observations"]), 200)
            self.assertEqual(output["validation_capture"]["coverage_end_utc"], at("11:00:00").isoformat())

    def test_outage_uses_saved_target_hour_not_current_activity(self):
        journal = {"events": [event(i, "10:30:00") for i in range(3)] +
                   [event(i + 10, "12:30:00", "12:35:00") for i in range(20)], "coverage": []}
        entry = prediction()
        result = score(entry, journal)
        self.assertEqual(result["result"], "hit")
        self.assertEqual(result["observations"], 3)
        self.assertEqual(entry["target_window"]["end_utc"], at("11:00:00").isoformat())

    def test_later_activity_never_verifies_an_old_prediction(self):
        result = score(prediction(), {"events": [event(i, "12:30:00", "12:35:00") for i in range(10)], "coverage": []})
        self.assertEqual(result["result"], "unconfirmed")
        self.assertEqual(result["observations"], 0)

    def test_exact_window_boundaries_and_late_arrivals(self):
        events = [event(1, "09:59:59"), event(2, "10:00:00"), event(3, "10:59:59"),
                  event(4, "11:00:00"), event(5, "10:30:00", "11:15:01")]
        self.assertEqual(score(prediction(), {"events": events, "coverage": []})["observations"], 2)

    def test_waits_until_end_plus_ingestion_grace(self):
        entry = prediction()
        self.assertIsNone(score(entry, {"events": [], "coverage": []}, "11:14:59"))
        self.assertEqual(entry["validation_status"], "pending")
        self.assertIsNotNone(score(entry, {"events": [], "coverage": []}, "11:15:00"))

    def test_invalid_or_naive_prediction_timestamp_is_not_replaced_by_now(self):
        for stamp in (None, "invalid", "2026-11-16T10:00:00"):
            entry = dict(prediction(), generated_at_utc=stamp)
            self.assertIsNone(score(entry, {"events": [], "coverage": []}))
            self.assertEqual(entry["validation_status"], "invalid_prediction_timestamp")

    def test_failure_requires_coverage_of_entire_target_interval(self):
        interval = {"start": at("10:00:00").isoformat(), "end": at("11:00:00").isoformat(),
                    "received_at_utc": at("11:05:00").isoformat(), "regions": ["peninsula"]}
        self.assertEqual(score(prediction(), {"events": [], "coverage": [interval]})["result"], "failure")
        interval["start"] = at("10:01:00").isoformat()
        self.assertEqual(score(prediction(), {"events": [], "coverage": [interval]})["result"], "unconfirmed")

    def test_overlapping_coverage_merges_but_gaps_do_not(self):
        intervals = [{"start": at(a).isoformat(), "end": at(b).isoformat(),
                      "received_at_utc": at("11:05:00").isoformat(), "regions": ["peninsula"]}
                     for a, b in [("09:50:00", "10:30:00"), ("10:25:00", "11:05:00")]]
        self.assertTrue(covers(intervals, at("10:00:00"), at("11:00:00"), "peninsula", at("11:15:00")))
        intervals[1]["start"] = at("10:31:00").isoformat()
        self.assertFalse(covers(intervals, at("10:00:00"), at("11:00:00"), "peninsula", at("11:15:00")))

    def test_journal_deduplicates_across_runs_and_rejects_future_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            psk = {"validation_capture": {"version": TIMING_VERSION, "completed_at": at("11:05:00").isoformat(),
                                          "events": [event(1, "10:30:00"), event(2, "12:00:00")]}}
            update_journal(path, psk, at("11:06:00"))
            psk["validation_capture"]["completed_at"] = at("11:10:00").isoformat()
            journal = update_journal(path, psk, at("11:11:00"))
            self.assertEqual(len(journal["events"]), 1)
            self.assertEqual(journal["events"][0]["received_at_utc"], at("11:05:00").isoformat())

    def test_collector_keeps_all_events_and_does_not_claim_capped_query_complete(self):
        rows = [{"timestamp_utc": at("10:30:00").isoformat(), "frequency_hz": 14074000,
                 "sender_callsign": f"EA1A{i}", "receiver_callsign": "EA2ABC", "mode": "FT8",
                 "regions": ["peninsula"], "band": "20m"} for i in range(250)]
        query = {"status": "ok", "report_count": 250, "started_at": at("11:00:00").isoformat(),
                 "finished_at": at("11:00:02").isoformat()}
        result = validation_capture(rows, {"IN": query}, at("11:00:03").isoformat())
        self.assertEqual(len(result["events"]), 250)
        self.assertEqual(result["coverage_start_utc"], at("10:00:03").isoformat())
        query["report_count"] = 5000
        self.assertIsNone(validation_capture(rows, {"IN": query}, at("11:00:03").isoformat())["coverage_start_utc"])

    def test_size_eviction_also_removes_coverage_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            events = [dict(event(i, f"10:{i:02d}:00"), id=str(i) + "x" * 100) for i in range(50)]
            capture = {"version": TIMING_VERSION, "completed_at": at("11:05:00").isoformat(), "events": events,
                       "coverage_start_utc": at("10:00:00").isoformat(), "coverage_end_utc": at("11:00:00").isoformat()}
            journal = update_journal(path, {"validation_capture": capture, "regions": {"peninsula": {"status": "ok"}}}, at("11:06:00"), max_bytes=2048)
            self.assertLessEqual(path.stat().st_size, 2048)
            self.assertLess(len(journal["events"]), 50)
            self.assertFalse(covers(journal["coverage"], at("10:00:00"), at("11:00:00"), "peninsula", at("11:15:00")))

    def test_main_preserves_legacy_but_excludes_scores_and_reloads_journal(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return at("13:00:00")
        with tempfile.TemporaryDirectory() as directory:
            import os
            root = Path(directory)
            data = root / "public" / "data"
            legacy_score = {"peninsula": {"20m": {"result": "hit", "observations": 999}}}
            legacy = dict(prediction(), evaluation=copy.deepcopy(legacy_score))
            legacy.pop("validation_method_version")
            save_history(data / "prediction-history.json", {"entries": [legacy, prediction()]}, active_cycles=1)
            (data / "prediction-observations.json").write_text(json.dumps({"events": [event(i, "10:30:00") for i in range(3)], "coverage": []}), encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch("scripts.validate_prediction_history.datetime", Clock):
                    main()
            finally:
                os.chdir(previous)
            history = load_history(data / "prediction-history.json")
            self.assertEqual(history["entries"][0]["legacy_evaluation"], legacy_score)
            self.assertIsNone(history["entries"][0]["evaluation"])
            self.assertEqual(history["summary"]["peninsula"]["20m"]["hits"], 1)
            self.assertEqual(history["summary"]["peninsula"]["20m"]["observations_total"], 3)
            self.assertEqual(history["temporal_validation"]["legacy_entries_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
