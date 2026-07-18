import unittest

from scripts.regionalization import (
    PENINSULA_SUBREGIONS,
    REGION_POINT_NAMES,
    SPAIN_POINTS,
    build_region_summaries,
)


def synthetic_points():
    result = []
    for index, name in enumerate(SPAIN_POINTS):
        result.append(
            {
                "name": name,
                "latitude": SPAIN_POINTS[name][0],
                "longitude": SPAIN_POINTS[name][1],
                "fof2_mhz": 3.0 + index / 10,
                "mufd_mhz": 10.0 + index,
            }
        )
    return result


class RegionalizationTests(unittest.TestCase):
    def test_region_point_counts_are_representative(self):
        self.assertEqual(len(REGION_POINT_NAMES["mainland"]), 14)
        self.assertEqual(len(REGION_POINT_NAMES["balearics"]), 3)
        self.assertEqual(len(REGION_POINT_NAMES["canaries"]), 5)

    def test_peninsula_has_four_non_overlapping_subregions(self):
        names = [
            name
            for definition in PENINSULA_SUBREGIONS.values()
            for name in definition["points"]
        ]
        self.assertEqual(len(PENINSULA_SUBREGIONS), 4)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(REGION_POINT_NAMES["mainland"]))

    def test_baleares_and_canarias_cover_east_and_west(self):
        self.assertIn("Ibiza", REGION_POINT_NAMES["balearics"])
        self.assertIn("Mahon", REGION_POINT_NAMES["balearics"])
        self.assertIn("Arrecife", REGION_POINT_NAMES["canaries"])
        self.assertIn("Santa_Cruz_La_Palma", REGION_POINT_NAMES["canaries"])

    def test_summary_exposes_spread_and_subregions(self):
        regions = build_region_summaries(synthetic_points())

        self.assertEqual(regions["mainland"]["summary"]["points"], 14)
        self.assertEqual(regions["balearics"]["summary"]["points"], 3)
        self.assertEqual(regions["canaries"]["summary"]["points"], 5)
        self.assertEqual(len(regions["mainland"]["subregions"]), 4)
        self.assertGreater(
            regions["mainland"]["summary"]["mufd_mhz"]["spread"], 0
        )

    def test_missing_point_fails_instead_of_silently_biasing_region(self):
        points = [
            point
            for point in synthetic_points()
            if point["name"] != "Santander"
        ]
        with self.assertRaisesRegex(ValueError, "Santander"):
            build_region_summaries(points)


if __name__ == "__main__":
    unittest.main()
