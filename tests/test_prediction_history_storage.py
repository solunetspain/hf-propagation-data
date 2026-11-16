import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_prediction_history import load_history, save_history, main


class HistoryStorageTests(unittest.TestCase):
    def test_legacy_migration_round_trip_and_bounded_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prediction-history.json"
            entries = [{"id": i, "text": "España áéí" * 30} for i in range(20)]
            history = {"entries": entries, "summary": {"hits": 42}}
            path.write_text(json.dumps(history), encoding="utf-8")
            save_history(path, load_history(path), active_cycles=3, max_bytes=2048)
            working = json.loads(path.read_text(encoding="utf-8"))
            self.assertLessEqual(len(working["entries"]), 3)
            self.assertGreater(len(working["entry_archives"]), 1)
            self.assertEqual(load_history(path)["entries"], entries)
            self.assertEqual(load_history(path)["summary"], {"hits": 42})
            for file in Path(directory).rglob("*.json"):
                self.assertLessEqual(file.stat().st_size, 2048)
            restored = load_history(path)
            restored["entries"].append({"id": 20})
            save_history(path, restored, active_cycles=3, max_bytes=2048)
            self.assertEqual(load_history(path)["entries"], entries + [{"id": 20}])

    def test_missing_or_corrupt_archive_fails_instead_of_losing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prediction-history.json"
            save_history(path, {"entries": [{"id": i} for i in range(8)]}, active_cycles=2)
            archive = next((path.parent / "prediction-history-archive").glob("*.json"))
            original = archive.read_bytes()
            archive.write_bytes(b'{}')
            with self.assertRaises(ValueError):
                load_history(path)
            archive.write_bytes(original)
            archive.unlink()
            with self.assertRaises(FileNotFoundError):
                load_history(path)

    def test_active_file_is_bounded_even_for_large_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prediction-history.json"
            history = {"entries": [{"data": "x" * 500} for _ in range(12)]}
            save_history(path, history, active_cycles=1000, max_bytes=4096)
            self.assertEqual(load_history(path)["entries"], history["entries"])
            self.assertLessEqual(path.stat().st_size, 4096)

    def test_scoring_retains_archived_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "public" / "data"
            path = data / "prediction-history.json"
            entries = [{"generated_at_utc": "2026-01-01T00:00:00+00:00", "recommendations": {}} for _ in range(5)]
            save_history(path, {"entries": entries}, active_cycles=1)
            import os
            previous = Path.cwd()
            try:
                os.chdir(root)
                main()
            finally:
                os.chdir(previous)
            restored = load_history(path)
            self.assertEqual(len(restored["entries"]), 6)
            self.assertTrue(all(item["evaluation"] is not None for item in restored["entries"][:-1]))


if __name__ == "__main__":
    unittest.main()
