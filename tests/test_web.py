import unittest

from ai_web_agent.web import _job_from_payload, _valid_run_id


class WebInputTests(unittest.TestCase):
    def test_builds_job_from_prompt_only(self):
        job = _job_from_payload({"prompt": "Annual report for ACME GmbH 2025"})
        self.assertEqual(job.objective, "Annual report for ACME GmbH 2025")
        self.assertTrue(job.allow_unscanned)

    def test_rejects_old_multi_field_payload(self):
        with self.assertRaisesRegex(ValueError, "prompt"):
            _job_from_payload({"objective": "Retrieve page", "urls": []})

    def test_run_id_validation(self):
        self.assertTrue(_valid_run_id("a" * 32))
        self.assertFalse(_valid_run_id("../audit.sqlite3"))


if __name__ == "__main__":
    unittest.main()
