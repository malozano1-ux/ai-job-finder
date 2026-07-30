import json
import tempfile
import unittest
from pathlib import Path

import job_finder_agent as agent
from sync_applied_jobs import rows_to_jobs
from sync_internship_tracker import tracker_rows


class AgentTests(unittest.TestCase):
    def test_read_cv_accepts_private_text_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cv.txt"
            path.write_text("Python, SQL, and data science", encoding="utf-8")
            self.assertEqual(agent.read_cv(path), "Python, SQL, and data science")

    def test_canonical_url_removes_tracking(self):
        actual = agent.canonical_url(
            "https://example.com/job/1/?utm_source=x&ref=y&team=data"
        )
        self.assertEqual(actual, "https://example.com/job/1?team=data")

    def test_amazon_url_uses_english_locale(self):
        actual = agent.canonical_url("https://www.amazon.jobs/cs/jobs/123/example")
        self.assertEqual(actual, "https://www.amazon.jobs/en/jobs/123/example")

    def test_extract_json_accepts_fenced_json(self):
        self.assertEqual(agent.extract_json('```json\n{"jobs": []}\n```'), {"jobs": []})

    def test_history_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            original = agent.HISTORY_FILE
            agent.HISTORY_FILE = Path(directory) / "missing.json"
            try:
                self.assertEqual(agent.load_recent_urls(), set())
            finally:
                agent.HISTORY_FILE = original

    def test_applied_job_is_excluded_by_normalized_identity(self):
        self.assertEqual(
            agent.normalize_identity("Data Scientist, New Grad"),
            agent.normalize_identity("Data Scientist - New Grad"),
        )

    def test_tracker_rows_are_deduplicated(self):
        rows = [
            ["SentiLink", "Data Scientist, New Grad", "https://example.com/job"],
            ["SentiLink", "Data Scientist, New Grad", "https://example.com/job"],
            ["Missing title"],
        ]
        self.assertEqual(
            rows_to_jobs(rows),
            [{
                "company": "SentiLink",
                "title": "Data Scientist, New Grad",
                "url": "https://example.com/job",
            }],
        )

    def test_internship_tracker_rows_use_discovered_stage(self):
        rows = tracker_rows({"jobs": [{
            "company": "Example",
            "title": "Data Scientist Intern",
            "url": "https://example.com/internship",
            "fit_score": 90,
        }]})
        self.assertEqual(rows[0][0:5], [
            "Example",
            "Data Scientist Intern",
            "https://example.com/internship",
            "",
            "Discovered",
        ])


if __name__ == "__main__":
    unittest.main()
