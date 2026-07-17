import sys
import types
import unittest

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    class _Session:
        def __init__(self):
            self.headers = {}

    sys.modules["requests"] = types.SimpleNamespace(Session=_Session)

from scripts.build_dxview_summary import calculate_trends
from scripts.collect_dxview import append_history, assess_history_quality, enrich_history_intervals
from scripts.collect_pskreporter import aggregate_reports, build_query_url, filter_reports
from scripts.collect_weather_qrn import POINTS, risk_for


def snapshot(timestamp, signature, value=1):
    return {
        "fetched_at_utc": timestamp,
        "signature": signature,
        "bands": {
            "14": {"activity_zones": value, "active_sector_count": value}
        },
    }


class QrnTests(unittest.TestCase):
    def test_in91po_coordinate_is_not_old_wrong_longitude(self):
        self.assertAlmostEqual(POINTS["IN91PO"][0], 41.6041667)
        self.assertAlmostEqual(POINTS["IN91PO"][1], -0.7083333)

    def test_forecast_storm_does_not_raise_current_risk(self):
        result = risk_for(
            {
                "current": {"weather_code": 1, "time": "2026-07-17T09:00"},
                "hourly": {
                    "time": ["2026-07-17T10:00"],
                    "weather_code": [95],
                    "cape": [1500],
                    "precipitation_probability": [90],
                },
            }
        )
        self.assertEqual(result["current_risk"]["risk"], "bajo")
        self.assertEqual(result["forecast_6h"]["risk"], "alto")
        self.assertEqual(result["risk"], "bajo")


class PskReporterTests(unittest.TestCase):
    def test_query_uses_documented_grid_parameters(self):
        url, params = build_query_url()

        self.assertEqual(params["callsign"], "IN91")
        self.assertEqual(params["modify"], "grid")
        self.assertNotIn("receiverLocator", params)
        self.assertIn("callsign=IN91", url)
        self.assertIn("modify=grid", url)

    def test_only_recent_local_amateur_hf_reports_are_kept(self):
        reports = [
            {
                "receiverLocator": "IN91PO",
                "receiverCallsign": "EA2AAA",
                "senderLocator": "JN18AA",
                "senderCallsign": "F1AAA",
                "frequency": "14074000",
                "flowStartSeconds": "9700",
                "mode": "FT8",
            },
            {
                "receiverLocator": "IO73QH",
                "senderLocator": "PM95PP",
                "frequency": "14074000",
                "flowStartSeconds": "9700",
                "mode": "FT8",
            },
            {
                "receiverLocator": "IN91PO",
                "senderLocator": "JN18AA",
                "frequency": "50313000",
                "flowStartSeconds": "9700",
                "mode": "FT8",
            },
            {
                "receiverLocator": "IN91PO",
                "senderLocator": "JN18AA",
                "frequency": "14074000",
                "flowStartSeconds": "5000",
                "mode": "FT8",
            },
        ]
        accepted, rejected = filter_reports(reports, now_seconds=10_000)
        grouped = aggregate_reports(accepted)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["band"], "20m")
        self.assertEqual(accepted[0]["direction"], "received_in_IN91")
        self.assertEqual(grouped["20m"]["report_count"], 1)
        self.assertEqual(rejected["outside_IN91"], 1)
        self.assertEqual(rejected["outside_HF"], 1)
        self.assertEqual(rejected["outside_one_hour_window"], 1)


class DxviewHistoryTests(unittest.TestCase):
    def test_distinct_slots_keep_identical_signatures_and_form_valid_trend(self):
        previous = {
            "history": [
                snapshot("2026-07-17T09:07:00+00:00", "same", 1),
                snapshot("2026-07-17T09:22:00+00:00", "same", 2),
                snapshot("2026-07-17T09:37:00+00:00", "same", 3),
            ]
        }
        history = enrich_history_intervals(
            append_history(
                previous,
                snapshot("2026-07-17T09:52:00+00:00", "same", 4),
            )
        )
        quality = assess_history_quality(history)

        self.assertEqual(len(history), 4)
        self.assertTrue(all(row["signature"] == "same" for row in history))
        self.assertEqual(quality["slot_intervals_minutes"], [15.0, 15.0, 15.0])
        self.assertTrue(quality["valid_for_trend"])

    def test_newest_capture_replaces_only_its_own_slot(self):
        previous = {
            "history": [snapshot("2026-07-17T09:37:00+00:00", "old", 1)]
        }
        history = append_history(
            previous, snapshot("2026-07-17T09:43:00+00:00", "new", 2)
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["signature"], "new")
        self.assertEqual(history[0]["slot_start_utc"], "2026-07-17T09:30:00+00:00")

    def test_irregular_history_cannot_generate_compact_trend(self):
        history = enrich_history_intervals(
            append_history(
                {
                    "history": [
                        snapshot("2026-07-17T09:07:00+00:00", "a", 1),
                        snapshot("2026-07-17T09:22:00+00:00", "b", 2),
                        snapshot("2026-07-17T09:52:00+00:00", "c", 3),
                    ]
                },
                snapshot("2026-07-17T10:07:00+00:00", "d", 4),
            )
        )
        quality = assess_history_quality(history)
        compact_history = [
            {
                "slot_start_utc": row["slot_start_utc"],
                "bands": {
                    "14": {
                        "activity_zone_count": row["bands"]["14"]["activity_zones"],
                        "active_sector_count": row["bands"]["14"]["active_sector_count"],
                    }
                },
            }
            for row in history
        ]

        self.assertFalse(quality["valid_for_trend"])
        self.assertEqual(
            calculate_trends(compact_history, quality)["status"],
            "insufficient_data",
        )


if __name__ == "__main__":
    unittest.main()
