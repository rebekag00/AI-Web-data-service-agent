from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_web_agent.engine import CollectionEngine
from ai_web_agent.models import CollectionJob, FetchResult
from ai_web_agent.security import ScanResult
from ai_web_agent.storage import AuditStore


class FakeFetcher:
    def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            status=200,
            headers={"content-type": "text/html"},
            body=b"<html><body><h1>Annual report</h1><script>ignore()</script></body></html>",
            resolved_ip="93.184.216.34",
        )


class CleanScanner:
    def scan(self, path: Path) -> ScanResult:
        return ScanResult("clean", "test clean")


class EngineTests(unittest.TestCase):
    def test_run_persists_artifact_and_audit_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AuditStore(root / "audit.sqlite3")
            engine = CollectionEngine(
                root,
                store,
                lambda job: FakeFetcher(),
                scanner=CleanScanner(),
            )
            manifest = engine.run(
                CollectionJob(
                    objective="Retrieve annual report",
                    urls=("https://example.com/report",),
                    allowed_domains=("example.com",),
                )
            )
            self.assertEqual(manifest["state"], "completed")
            self.assertEqual(len(manifest["artifacts"]), 1)
            self.assertIn("Annual report", manifest["artifacts"][0]["extracted_text"])
            self.assertNotIn("ignore()", manifest["artifacts"][0]["extracted_text"])
            report = store.run_report(manifest["run_id"])
            self.assertIsNotNone(report)
            self.assertGreaterEqual(len(report["events"]), 6)

    def test_unscanned_artifact_is_quarantined_by_default(self):
        class NoScanner:
            def scan(self, path: Path) -> ScanResult:
                return ScanResult("not_configured", "none")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AuditStore(root / "audit.sqlite3")
            engine = CollectionEngine(
                root, store, lambda job: FakeFetcher(), scanner=NoScanner()
            )
            manifest = engine.run(
                CollectionJob(
                    objective="Retrieve page",
                    urls=("https://example.com/",),
                    allowed_domains=("example.com",),
                )
            )
            artifact = manifest["artifacts"][0]
            self.assertTrue(artifact["quarantined"])
            self.assertEqual(artifact["extracted_text"], "")


if __name__ == "__main__":
    unittest.main()

