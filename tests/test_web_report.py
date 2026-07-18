import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_web_report import BLOCK_TITLES, build_report


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def metric(fof2, muf):
    return {
        "points": 3,
        "fof2_mhz": {"median": fof2, "min": fof2 - 0.2, "max": fof2 + 0.2, "spread": 0.4},
        "mufd_mhz": {"median": muf, "min": muf - 1, "max": muf + 1, "spread": 2},
    }


def write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


class WebReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        subregions = {
            "north": {"label": "Noroeste y Cantábrico", "summary": metric(5.2, 18.0)},
            "interior": {"label": "Interior y valle del Ebro", "summary": metric(5.0, 17.5)},
            "med": {"label": "Mediterráneo", "summary": metric(5.3, 18.5)},
            "south": {"label": "Sur y suroeste", "summary": metric(4.8, 17.0)},
        }
        write(self.data / "kc2g-spain.json", {
            "generated_at": NOW.isoformat(),
            "timestamp_utc": NOW.isoformat(),
            "status": "ok",
            "regions": {
                "mainland": {"summary": metric(5.1, 18.0), "subregions": subregions},
                "balearics": {"summary": metric(5.4, 19.0)},
                "canaries": {"summary": metric(6.1, 24.0)},
            },
        })
        write(self.data / "noaa-summary.json", {
            "generated_at": NOW.isoformat(), "status": "ok",
            "current": {
                "scales": {"R": "R0", "S": "S0", "G": "G0"},
                "geomagnetic": {"kp": 2, "a_index": 5},
                "xray": {"class": "B4.0"},
                "solar_flux": {"observed_flux_sfu": 155},
                "sunspots": {"sunspot_number": 88},
            },
            "drap": {"points": {
                "Galicia": {"highest_frequency_affected_1db_mhz": 2.0},
                "Cantabrico": {"highest_frequency_affected_1db_mhz": 2.1},
                "Centro": {"highest_frequency_affected_1db_mhz": 1.8},
                "Mediterraneo": {"highest_frequency_affected_1db_mhz": 1.7},
                "Andalucia": {"highest_frequency_affected_1db_mhz": 2.2},
                "Baleares": {"highest_frequency_affected_1db_mhz": 1.5},
                "Canarias": {"highest_frequency_affected_1db_mhz": 1.4},
            }},
        })
        write(self.data / "qrn-spain-summary.json", {
            "generated_at": NOW.isoformat(), "status": "ok",
            "points": {
                name: {"current_risk": {"risk": "bajo"}}
                for name in ("Galicia", "Cantabrico", "Centro", "Mediterraneo", "Andalucia", "Baleares", "Canarias")
            },
        })
        write(self.data / "dxview-in91po-summary.json", {
            "generated_at": NOW.isoformat(), "status": "ok",
            "bands": {"14": {"band_mhz": 14, "activity_zone_count": 3}},
        })
        write(self.data / "pskreporter-hf-summary.json", {
            "generated_at": NOW.isoformat(), "status": "ok",
            "bands": {"20m": {"report_count": 4}},
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_regions_and_all_exact_blocks(self):
        report = build_report(self.data, NOW)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(set(report["regions"]), {"peninsula", "baleares", "canarias"})
        for region in report["regions"].values():
            markdown = region["report_markdown"]
            for number, title in enumerate(BLOCK_TITLES):
                self.assertIn(f"## {number}. {title}", markdown)
            self.assertNotIn("tontos", markdown.casefold())

    def test_in91_observations_are_not_attributed_to_islands(self):
        report = build_report(self.data, NOW)
        self.assertIn("DXView: 14 MHz", report["regions"]["peninsula"]["report_markdown"])
        for key in ("baleares", "canarias"):
            markdown = report["regions"][key]["report_markdown"]
            self.assertIn("No se reutilizan DXView ni PSKReporter de IN91/IN91PO", markdown)
            self.assertIn("IN91/IN91PO, no regional", markdown)

    def test_missing_sources_do_not_create_numeric_measurements(self):
        empty = Path(tempfile.mkdtemp())
        report = build_report(empty, NOW)
        markdown = report["regions"]["peninsula"]["report_markdown"]
        self.assertIn("no validado", markdown)
        self.assertNotIn("155.0 sfu", markdown)
        self.assertNotIn("88", markdown)
        self.assertEqual(report["status"], "degraded")

    def test_subregional_warning_only_when_band_conclusions_differ(self):
        report = build_report(self.data, NOW)
        self.assertNotIn("**Aviso subregional:**", report["regions"]["peninsula"]["report_markdown"])
        kc2g = json.loads((self.data / "kc2g-spain.json").read_text())
        kc2g["regions"]["mainland"]["subregions"]["north"]["summary"] = metric(7.0, 30.0)
        write(self.data / "kc2g-spain.json", kc2g)
        report = build_report(self.data, NOW)
        self.assertIn("**Aviso subregional:**", report["regions"]["peninsula"]["report_markdown"])

    def test_validity_window_is_ninety_minutes(self):
        report = build_report(self.data, NOW)
        self.assertEqual(report["generated_at_utc"], "2026-07-18T12:00:00+00:00")
        self.assertEqual(report["valid_until_utc"], "2026-07-18T13:30:00+00:00")

    def test_stale_kc2g_has_zero_weight_and_no_measurements(self):
        kc2g = json.loads((self.data / "kc2g-spain.json").read_text())
        kc2g["freshness"] = "stale"
        write(self.data / "kc2g-spain.json", kc2g)
        report = build_report(self.data, NOW)
        markdown = report["regions"]["peninsula"]["report_markdown"]
        self.assertIn("| KC2G | regional | 0 min | 0 |", markdown)
        self.assertIn("foF2 regional no validado", markdown)
        self.assertEqual(report["regions"]["peninsula"]["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
