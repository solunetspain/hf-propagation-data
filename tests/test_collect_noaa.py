import unittest

from scripts.collect_noaa import (
    parse_daily_solar_indices,
    parse_drap,
    rtsw_summary,
)


class NoaaParserTests(unittest.TestCase):
    def test_rtsw_uses_only_active_good_quality_source(self):
        data = [
            {
                "time_tag": "2026-07-17T08:40:00",
                "active": True,
                "source": "SOLAR1",
                "overall_quality": 0,
                "bz_gsm": -2.0,
                "bt": 4.0,
            },
            {
                "time_tag": "2026-07-17T08:41:00",
                "active": True,
                "source": "SOLAR1",
                "overall_quality": 0,
                "bz_gsm": -4.0,
                "bt": 6.0,
            },
            {
                "time_tag": "2026-07-17T08:42:00",
                "active": False,
                "source": "ACE",
                "overall_quality": 0,
                "bz_gsm": 99.0,
                "bt": 99.0,
            },
            {
                "time_tag": "2026-07-17T08:42:00",
                "active": True,
                "source": "SOLAR1",
                "overall_quality": 1,
                "bz_gsm": 88.0,
                "bt": 88.0,
            },
        ]

        result = rtsw_summary(data, {"bz_gsm": "bz_gsm_nt", "bt": "bt_nt"})

        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "SOLAR1")
        self.assertEqual(result["samples"], 2)
        self.assertEqual(result["bz_gsm_nt"], -3.0)
        self.assertEqual(result["bt_nt"], 5.0)

    def test_daily_solar_indices_exposes_explicit_ssn(self):
        text = """
# Date Radio Flux Sunspot Number
2026 07 15  100  25  400  0 -999 * 4 0 0 2 0 0 0
2026 07 16  101  47  340  2 -999 * 2 0 0 0 0 0 0
"""
        result = parse_daily_solar_indices(text)

        self.assertEqual(result["date"], "2026-07-16")
        self.assertEqual(result["f107_sfu"], 101.0)
        self.assertEqual(result["sunspot_number"], 47.0)

    def test_drap_extracts_in91po_and_spain_summary(self):
        longitudes = list(range(-178, 179, 4))
        row_41 = [0.0] * len(longitudes)
        row_39 = [0.0] * len(longitudes)
        row_41[longitudes.index(-2)] = 1.2
        row_39[longitudes.index(-2)] = 1.0
        text = "\n".join(
            [
                "# DRAP Tabular Values",
                "# Product Valid At : 2026-07-17 08:45 UTC",
                "# X-RAY Message : Normal X-ray Background",
                " ".join(str(value) for value in longitudes),
                "41 | " + " ".join(str(value) for value in row_41),
                "39 | " + " ".join(str(value) for value in row_39),
            ]
        )

        result = parse_drap(
            text,
            {
                "IN91PO": (41.6041667, -0.7083333),
                "Centro": (40.0, -1.0),
            },
        )

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["timestamp_utc"], "2026-07-17T08:45:00+00:00")
        self.assertEqual(
            result["points"]["IN91PO"]["highest_frequency_affected_1db_mhz"],
            1.2,
        )
        self.assertEqual(result["spain"]["maximum_mhz"], 1.2)


if __name__ == "__main__":
    unittest.main()
