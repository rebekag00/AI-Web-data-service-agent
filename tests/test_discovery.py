from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_web_agent.discovery import (
    PromptDiscoveryEngine,
    SearchCandidate,
    SearchIntent,
    build_queries,
    choose_ares_company,
    fold_text,
    parse_prompt,
    validate_candidate,
)
from ai_web_agent.models import FetchResult, PromptJob
from ai_web_agent.security import ScanResult
from ai_web_agent.storage import AuditStore


class CleanScanner:
    def scan(self, path: Path) -> ScanResult:
        return ScanResult("clean", "test clean")


class RetryingSearch:
    def search(self, query: str, limit: int) -> list[SearchCandidate]:
        if "annual report" in query:
            return [SearchCandidate("https://wrong.example/2024", "ERSTE DIGITAL annual report 2024", "", query, 1)]
        return [SearchCandidate("https://good.example/2025", "ERSTE DIGITAL GmbH Jahresabschluss 2025", "", query, 1)]


class NoopResolver:
    def resolve(self, intent: SearchIntent) -> SearchIntent:
        return intent


class DiscoveryTests(unittest.TestCase):
    def test_parses_company_and_year_from_natural_prompt(self):
        intent = parse_prompt("I want to see the annual report for ERSTE DIGITAL GMBH 2025")
        self.assertEqual(intent.company, "ERSTE DIGITAL GMBH")
        self.assertEqual(intent.year, 2025)

    def test_requires_year(self):
        with self.assertRaisesRegex(ValueError, "year"):
            parse_prompt("Find the annual report for ERSTE DIGITAL GMBH")

    def test_parses_czech_prompt_and_builds_czech_intent(self):
        intent = parse_prompt("Chci vidět výroční zprávu společnosti ČEZ Distribuce, a. s. za rok 2024")
        self.assertEqual(intent.company, "ČEZ DISTRIBUCE A. S")
        self.assertEqual(intent.year, 2024)
        self.assertEqual(intent.language, "cs")
        self.assertEqual(intent.search_language, "cs")
        self.assertEqual(intent.document_type, "výroční zpráva")

    def test_english_prompt_can_build_czech_queries_after_company_resolution(self):
        intent = SearchIntent(
            company="ČEZ DISTRIBUCE, A. S.",
            year=2024,
            language="en",
            search_language="cs",
            jurisdiction="CZ",
            company_id="24729035",
        )
        queries = build_queries(intent)
        self.assertIn('"vyrocni zprava"', fold_text(queries[0]))
        self.assertTrue(any("účetní závěrka" in query for query in queries))

    def test_selects_confident_ares_match_without_diacritics(self):
        match = choose_ares_company("CEZ Distribuce a. s.", [
            {"ico": "1", "obchodniJmeno": "ČEZ Distribuce, a. s."},
            {"ico": "2", "obchodniJmeno": "ČEZ Distribuční služby, s.r.o."},
        ])
        self.assertEqual(match["ico"], "1")

    def test_validation_rejects_wrong_year(self):
        intent = parse_prompt("annual report for ERSTE DIGITAL GMBH 2025")
        candidate = SearchCandidate("https://example.com/r", "ERSTE DIGITAL annual report 2024", "", "q", 1)
        fetched = FetchResult(candidate.url, candidate.url, 200, {"content-type": "text/html"}, b"", "93.184.216.34")
        result = validate_candidate(intent, candidate, fetched, "ERSTE DIGITAL GmbH annual report 2024", "text/html")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["year_match"])

    def test_retries_then_selects_correct_year(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = PromptDiscoveryEngine(
                root,
                AuditStore(root / "audit.sqlite3"),
                RetryingSearch(),
                CleanScanner(),
                NoopResolver(),
            )

            def fake_fetch(candidate, limits):
                body = (
                    b"ERSTE DIGITAL GmbH annual report 2024"
                    if "2024" in candidate.url
                    else b"ERSTE DIGITAL GmbH Jahresabschluss annual report 2025"
                )
                return FetchResult(candidate.url, candidate.url, 200, {"content-type": "text/plain"}, body, "93.184.216.34")

            engine._fetch_candidate = fake_fetch
            manifest = engine.run(PromptJob("I want to see the annual report for ERSTE DIGITAL GMBH 2025"))
            self.assertEqual(len(manifest["search_attempts"]), 3)
            self.assertEqual(manifest["selected_source"]["url"], "https://good.example/2025")
            self.assertTrue(manifest["selected_source"]["year_match"])
            self.assertEqual(manifest["delivery_status"], "verified_source_report_restricted_or_summary")


if __name__ == "__main__":
    unittest.main()
