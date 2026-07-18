from __future__ import annotations

import unittest

from scripts.collect_dxview_regions import aggregate_region, sample_definitions
from scripts.collect_pskreporter_regions import (
    call_area,
    endpoint_region,
    locator_region,
    normalized_callsign,
)


class RegionalPSKReporterTests(unittest.TestCase):
    def test_spanish_call_areas(self):
        self.assertEqual(call_area("EA6ABC"), 6)
        self.assertEqual(call_area("EA8/EA2XYZ"), 8)
        self.assertEqual(call_area("EA9ABC"), 9)
        self.assertIsNone(call_area("F1ABC"))

    def test_region_classification(self):
        self.assertEqual(endpoint_region("EA3ABC", "JN11"), "peninsula")
        self.assertEqual(endpoint_region("EA6ABC", ""), "baleares")
        self.assertEqual(endpoint_region("EA8ABC", ""), "canarias")
        self.assertIsNone(endpoint_region("EA9ABC", "IM75"))

    def test_locator_cross_check(self):
        self.assertEqual(locator_region("JM19"), "baleares")
        self.assertEqual(locator_region("IL28"), "canarias")
        self.assertEqual(locator_region("IN80"), "peninsula")

    def test_portable_callsign_normalization(self):
        self.assertEqual(normalized_callsign("F/EA2ABC/P"), "EA2ABC")


class RegionalDXViewTests(unittest.TestCase):
    def test_region_samples_use_multiple_unique_buckets(self):
        regions, unique = sample_definitions()
        self.assertGreaterEqual(len(unique), 5)
        self.assertGreater(len(regions["peninsula"]), 1)
        self.assertEqual(len(regions["baleares"]), 1)
        self.assertEqual(len(regions["canarias"]), 1)

    def test_aggregate_region(self):
        samples = [{"bucket_id": 1}, {"bucket_id": 2}]
        fetched = {
            1: {
                14: {
                    "signature": "a",
                    "summary": {
                        "classification": {"activity": 4, "muf": 1},
                        "mode_zone_counts": {"digital": 2},
                        "sectors": {
                            "W": {"activity_zones": 2},
                        },
                    },
                }
            },
            2: {
                14: {
                    "signature": "b",
                    "summary": {
                        "classification": {"activity": 2, "muf": 0},
                        "mode_zone_counts": {"digital": 1, "cw": 1},
                        "sectors": {
                            "W": {"activity_zones": 1},
                            "N": {"activity_zones": 1},
                        },
                    },
                }
            },
        }
        result = aggregate_region(samples, fetched)
        band = result["bands"]["14"]
        self.assertEqual(band["view_count"], 2)
        self.assertEqual(band["unique_response_count"], 2)
        self.assertEqual(band["activity_zone_count"]["median"], 3.0)
        self.assertEqual(band["mode_view_counts"]["digital"], 2)


if __name__ == "__main__":
    unittest.main()
