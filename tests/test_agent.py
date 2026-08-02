import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import job_finder_agent as agent
import uw_part_time_agent as uw_agent
from schedule_gate import latest_due_slot
from openai import RateLimitError
from sync_applied_jobs import rows_to_jobs
from sync_internship_tracker import tracker_rows
from sync_uw_part_time_tracker import tracker_rows as uw_tracker_rows
from sync_calendar_deadlines import resolve_deadline
from sync_gmail_tracker import HEADERS, validate_plan


class AgentTests(unittest.TestCase):
    def test_schedule_gate_uses_latest_due_local_slot(self):
        moment = __import__("datetime").datetime(
            2026, 8, 1, 14, 20,
            tzinfo=__import__("zoneinfo").ZoneInfo("America/Los_Angeles"),
        )
        self.assertEqual(latest_due_slot(moment, [8, 13, 18]), "2026-08-01-13")

    def test_schedule_gate_catches_previous_evening_slot(self):
        moment = __import__("datetime").datetime(
            2026, 8, 1, 2, 20,
            tzinfo=__import__("zoneinfo").ZoneInfo("America/Los_Angeles"),
        )
        self.assertEqual(latest_due_slot(moment, [9, 14, 19]), "2026-07-31-19")

    @patch.object(uw_agent, "create_response_with_rate_limit_retry")
    @patch.object(uw_agent, "openai_client")
    def test_uw_search_uses_rate_limit_retry_and_token_cap(
        self,
        client_factory,
        create_response,
    ):
        create_response.return_value.output_text = json.dumps({
            "summary": {"reviewed": 0, "coverage": "test"},
            "jobs": [],
        })

        result = uw_agent.find_uw_jobs("test CV", [])

        self.assertEqual(result["jobs"], [])
        create_response.assert_called_once()
        args, kwargs = create_response.call_args
        self.assertIs(args[0], client_factory.return_value)
        self.assertEqual(kwargs["max_output_tokens"], 6_000)
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})

    def test_openai_rate_limit_is_retried(self):
        response = MagicMock()
        response.status_code = 429
        response.headers = {"retry-after": "0"}
        error = RateLimitError(
            "rate limit exceeded",
            response=response,
            body={"error": {"code": "rate_limit_exceeded"}},
        )
        expected = MagicMock()
        client = MagicMock()
        client.responses.create.side_effect = [error, expected]

        with patch.object(agent.time, "sleep") as sleep:
            actual = agent.create_response_with_rate_limit_retry(
                client,
                model="test-model",
                input="test prompt",
            )

        self.assertIs(actual, expected)
        self.assertEqual(client.responses.create.call_count, 2)
        sleep.assert_called_once_with(15.0)

    def test_openai_rate_limit_honors_message_wait_with_cushion(self):
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        error = RateLimitError(
            "Please try again in 13.149s.",
            response=response,
            body={"error": {"code": "rate_limit_exceeded"}},
        )
        client = MagicMock()
        client.responses.create.side_effect = [error, MagicMock()]

        with patch.object(agent.time, "sleep") as sleep:
            agent.create_response_with_rate_limit_retry(
                client,
                model="test-model",
                input="test prompt",
            )

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 18.149)

    def test_openai_rate_limit_stops_after_max_attempts(self):
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        error = RateLimitError(
            "rate limit exceeded",
            response=response,
            body={"error": {"code": "rate_limit_exceeded"}},
        )
        client = MagicMock()
        client.responses.create.side_effect = error

        with patch.object(agent.time, "sleep"), self.assertRaises(RateLimitError):
            agent.create_response_with_rate_limit_retry(
                client,
                max_attempts=2,
                model="test-model",
                input="test prompt",
            )

        self.assertEqual(client.responses.create.call_count, 2)

    def test_gmail_sync_routes_confirmation_to_existing_tab_row(self):
        rows = {
            "Internship Tracker": [
                HEADERS,
                ["Walleye Capital", "Investment Data Science Intern"] + [""] * 13,
            ],
            "Job Tracker": [HEADERS],
            "UW Part-Time": [HEADERS],
        }
        emails = [{"message_id": "gmail-1"}]
        plan = {"adds": [], "updates": [{
            "tab": "Internship Tracker",
            "row_number": 2,
            "stage": "Applied",
            "evidence_message_id": "gmail-1",
        }]}
        changes = validate_plan(plan, emails, rows)
        self.assertEqual(len(changes["updates"]), 1)
        self.assertEqual(changes["updates"][0]["tab"], "Internship Tracker")

    def test_gmail_sync_rejects_stage_downgrade(self):
        rows = {
            "Job Tracker": [
                HEADERS,
                ["Acme", "Data Scientist", "", "", "Final Interview"] + [""] * 10,
            ],
        }
        emails = [{"message_id": "old-confirmation"}]
        plan = {"adds": [], "updates": [{
            "tab": "Job Tracker",
            "row_number": 2,
            "stage": "Applied",
            "evidence_message_id": "old-confirmation",
        }]}
        changes = validate_plan(plan, emails, rows)
        self.assertEqual(changes["updates"], [])

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

    def test_uw_tracker_rows_use_discovered_stage(self):
        rows = uw_tracker_rows({"jobs": [{
            "company": "UW Libraries",
            "title": "Student Assistant",
            "url": "https://example.com/uw-job",
            "fit_score": 80,
        }]})
        self.assertEqual(rows[0][0:5], [
            "UW Libraries",
            "Student Assistant",
            "https://example.com/uw-job",
            "",
            "Discovered",
        ])

    def test_published_calendar_deadline_is_preserved(self):
        deadline, kind = resolve_deadline(
            "2027-03-15",
            today=__import__("datetime").date(2027, 3, 1),
        )
        self.assertEqual(deadline.isoformat(), "2027-03-15")
        self.assertEqual(kind, "Published application deadline")

    def test_missing_calendar_deadline_defaults_to_three_days(self):
        deadline, kind = resolve_deadline(
            "not stated",
            today=__import__("datetime").date(2027, 3, 1),
        )
        self.assertEqual(deadline.isoformat(), "2027-03-04")
        self.assertIn("Personal apply-by", kind)


if __name__ == "__main__":
    unittest.main()
