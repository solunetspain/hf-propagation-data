import unittest

from scripts.build_dxview_summary import summarize_band


class SummarizeBandTests(unittest.TestCase):
    def test_current_processed_schema_keeps_modes_sectors_and_distances(self):
        payload = {
            "requested_band_mhz": 14,
            "zone_count": 9,
            "classification": {"activity": 8, "muf": 1},
            "mode_zone_counts": {"digital": 6, "cw": 4, "ssb": 3},
            "active_sector_count": 2,
            "sectors": {
                "NE": {
                    "zones": 5,
                    "activity_zones": 4,
                    "muf_zones": 1,
                    "modes": {"digital": 4, "cw": 2, "ssb": 1},
                    "distance_km": {"min": 400.0, "median": 900.0, "max": 1400.0},
                },
                "SW": {
                    "zones": 4,
                    "activity_zones": 4,
                    "muf_zones": 0,
                    "modes": {"digital": 2, "cw": 2, "ssb": 2},
                    "distance_km": {"min": 700.0, "median": 1200.0, "max": 2000.0},
                },
            },
        }

        result = summarize_band("14", payload)

        self.assertEqual(result["activity_zone_count"], 8)
        self.assertEqual(result["muf_zone_count"], 1)
        self.assertEqual(result["mode_zone_counts"]["digital"], 6)
        self.assertEqual(result["digital_sector_count"], 2)
        self.assertEqual(result["cw_sector_count"], 2)
        self.assertEqual(result["ssb_sector_count"], 2)
        self.assertEqual(result["nearest_km"], 400.0)
        self.assertEqual(result["farthest_km"], 2000.0)
        self.assertEqual(result["main_sectors"][0]["sector"], "NE")
        self.assertEqual(result["main_sectors"][0]["median_km"], 900.0)

    def test_muf_view_is_not_misreported_as_activity(self):
        result = summarize_band(
            "0",
            {
                "requested_band_mhz": 0,
                "zone_count": 40,
                "classification": {"muf": 40},
                "active_sector_count": 0,
                "muf_sector_count": 8,
            },
        )

        self.assertEqual(result["activity_zone_count"], 0)
        self.assertEqual(result["muf_zone_count"], 40)


if __name__ == "__main__":
    unittest.main()
