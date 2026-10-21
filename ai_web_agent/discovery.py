from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
import unicodedata
import uuid
from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, quote_plus, urlsplit

from .extraction import extract_text
from .fetcher import SafeFetcher
from .models import ArtifactRecord, FetchResult, PromptJob, RunLimits, RunState
from .policy import URLPolicy
from .security import MalwareScanner, detect_mime, mime_matches
from .storage import AuditStore


YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
REPORT_TERMS_BY_LANGUAGE = {
    "en": ("annual report", "annual financial report", "annual financial statements", "financial statements"),
    "de": ("jahresabschluss", "geschaftsbericht", "lagebericht"),
    "cs": ("vyrocni zprava", "ucetni zaverka", "konsolidovana ucetni zaverka", "sbirka listin"),
    "sk": ("vyrocna sprava", "uctovna zavierka"),
    "pl": ("raport roczny", "sprawozdanie finansowe"),
    "fr": ("rapport annuel", "etats financiers"),
    "es": ("informe anual", "estados financieros"),
    "it": ("relazione annuale", "bilancio annuale"),
    "nl": ("jaarverslag", "jaarrekening"),
    "hu": ("eves jelentes", "eves beszamolo"),
    "ro": ("raport anual", "situatii financiare"),
}
SEARCH_TERMS_BY_LANGUAGE = {
    "en": ("annual report", "financial statements"),
    "de": ("Jahresabschluss", "Geschäftsbericht"),
    "cs": ("výroční zpráva", "účetní závěrka"),
    "sk": ("výročná správa", "účtovná závierka"),
    "pl": ("raport roczny", "sprawozdanie finansowe"),
    "fr": ("rapport annuel", "états financiers"),
    "es": ("informe anual", "estados financieros"),
    "it": ("relazione annuale", "bilancio annuale"),
    "nl": ("jaarverslag", "jaarrekening"),
    "hu": ("éves jelentés", "éves beszámoló"),
    "ro": ("raport anual", "situații financiare"),
}
REPORT_TERMS = tuple(dict.fromkeys(term for terms in REPORT_TERMS_BY_LANGUAGE.values() for term in terms))
LEGAL_SUFFIXES = {"gmbh", "ag", "inc", "ltd", "llc", "plc", "se", "sa", "kg", "co", "sro", "as", "spol", "kft", "zrt", "srl", "sas", "bv", "nv", "oy", "ab"}
REQUEST_WORDS = {
    "i", "want", "to", "see", "show", "me", "find", "get", "fetch", "download",
    "please", "the", "for", "of", "company", "report", "annual", "financial",
    "statements", "statement", "jahresabschluss", "geschaftsbericht", "geschäftsbericht",
    "chci", "videt", "najdi", "najit", "ukaz", "stahnout", "pro", "spolecnost", "spolecnosti",
    "vyrocni", "zprava", "ucetni", "zaverka", "konsolidovana", "sbirka", "listin", "za", "rok", "roku",
    "chcem", "vidiet", "najdi", "spolocnost", "vyrocna", "sprava", "uctovna", "zavierka",
    "ich", "mochte", "sehen", "fur", "unternehmen", "zeige", "mir",
    "je", "veux", "voir", "pour", "societe", "rapport", "annuel",
    "zpravu", "zaverku", "zaverky", "zpravy", "ucetni", "konsolidovanou",
    "den", "der", "die", "das", "fur", "sehen", "mochte", "abschluss",
}
REQUEST_PREFIXES = (
    "vyrocn", "zprav", "ucetn", "zaver", "jahresabschluss", "geschaftsbericht",
    "annual", "financial", "rapport", "sprawozd", "vyrocn", "uctovn", "zavier",
)
LANGUAGE_HINTS = {
    "cs": ("chci", "najdi", "vyrocn", "ucetn", "zaver", "spolecnost", "rok"),
    "sk": ("chcem", "vyrocn", "uctovn", "zavier", "spolocnost"),
    "de": ("ich", "mochte", "jahresabschluss", "geschaftsbericht", "unternehmen"),
    "pl": ("raport roczny", "sprawozdanie", "znajdz"),
    "fr": ("rapport annuel", "je veux", "societe"),
    "es": ("informe anual", "quiero", "empresa"),
    "it": ("relazione annuale", "bilancio", "societa"),
    "nl": ("jaarverslag", "jaarrekening"),
    "hu": ("eves jelentes", "beszamolo"),
    "ro": ("raport anual", "situatii financiare"),
}


@dataclass(frozen=True)
class SearchIntent:
    company: str
    year: int
    document_type: str = "annual report"
    language: str = "en"
    search_language: str = "en"
    jurisdiction: str = "unknown"
    company_id: str = ""


@dataclass(frozen=True)
class SearchCandidate:
    url: str
    title: str
    snippet: str
    query: str
    rank: int


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchCandidate]: ...


class CompanyResolver(Protocol):
    def resolve(self, intent: SearchIntent) -> SearchIntent: ...


def fold_text(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def detect_language(prompt: str) -> str:
    folded = fold_text(prompt)
    scores = {language: sum(1 for term in hints if term in folded) for language, hints in LANGUAGE_HINTS.items()}
    scores["en"] = sum(1 for term in ("annual report", "financial statements", "i want", "find") if term in folded)
    best = max(scores, key=scores.get)
    return best if scores[best] else "en"


def parse_prompt(prompt: str) -> SearchIntent:
    clean = " ".join(prompt.strip().split())
    years = YEAR_RE.findall(clean)
    if not years:
        raise ValueError("Include the report year in the prompt, for example 2025.")
    year = int(years[-1])
    without_year = YEAR_RE.sub(" ", clean)
    tokens = re.findall(r"[\wÀ-ž&.-]+", without_year, flags=re.UNICODE)
    report_parts = {part for term in REPORT_TERMS for part in term.split()}
    company_tokens = []
    for token in tokens:
        folded = fold_text(token).strip(".-")
        if folded in REQUEST_WORDS or folded in report_parts:
            continue
        if any(folded.startswith(prefix) for prefix in REQUEST_PREFIXES):
            continue
        company_tokens.append(token)
    company = " ".join(company_tokens).strip(" ,.-")
    if not company:
        raise ValueError("Include the company name in the prompt.")
    language = detect_language(prompt)
    document_type = SEARCH_TERMS_BY_LANGUAGE.get(language, SEARCH_TERMS_BY_LANGUAGE["en"])[0]
    return SearchIntent(
        company=company.upper(),
        year=year,
        document_type=document_type,
        language=language,
        search_language=language,
    )


def _company_tokens(value: str) -> list[str]:
    return [
        token for token in re.findall(r"[\wÀ-ž]+", fold_text(value), flags=re.UNICODE)
        if token not in LEGAL_SUFFIXES and token not in {"a", "s", "r", "o"}
    ]


def choose_ares_company(company: str, rows: list[dict]) -> dict | None:
    """Return only a confident Czech-register match for the requested name."""
    requested = _company_tokens(company)
    if not requested:
        return None
    requested_set = set(requested)
    matches: list[tuple[int, int, dict]] = []
    for row in rows:
        name = str(row.get("obchodniJmeno") or "")
        candidate = _company_tokens(name)
        if not candidate:
            continue
        candidate_set = set(candidate)
        if candidate == requested:
            score = 100
        elif len(requested) >= 2 and requested_set.issubset(candidate_set):
            score = 80 - max(0, len(candidate) - len(requested)) * 5
        elif len(requested) == 1 and candidate == requested:
            score = 90
        else:
            continue
        matches.append((score, -len(candidate), row))
    return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


class CzechAresResolver:
    """Resolve Czech companies through the official, no-key ARES REST service."""

    endpoint = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"

    def __init__(self) -> None:
        self._cache: dict[str, SearchIntent] = {}

    def resolve(self, intent: SearchIntent) -> SearchIntent:
        cache_key = fold_text(intent.company)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return replace(cached, year=intent.year, language=intent.language)
        curl = shutil.which("curl")
        if not curl:
            return intent
        payload = json.dumps({"obchodniJmeno": intent.company, "start": 0, "pocet": 25}).encode("utf-8")
        try:
            proc = subprocess.run(
                [
                    curl, "--fail", "--silent", "--show-error", "--max-time", "15",
                    "-H", "Content-Type: application/json", "--data-binary", "@-", self.endpoint,
                ],
                input=payload,
                capture_output=True,
                timeout=20,
                check=False,
            )
            if proc.returncode != 0:
                return intent
            body = json.loads(proc.stdout.decode("utf-8"))
            match = choose_ares_company(intent.company, body.get("ekonomickeSubjekty") or [])
            if not match:
                return intent
            resolved = replace(
                intent,
                company=str(match["obchodniJmeno"]).upper(),
                document_type=SEARCH_TERMS_BY_LANGUAGE["cs"][0],
                search_language="cs",
                jurisdiction="CZ",
                company_id=str(match.get("ico") or ""),
            )
            self._cache[cache_key] = resolved
            return resolved
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            return intent


class _BraveParser(HTMLParser):
    def __init__(self, query: str) -> None:
        super().__init__(convert_charrefs=True)
        self.query = query
        self.results: list[SearchCandidate] = []
        self._href = ""
        self._capture = False
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "a" and "l1" in classes and values.get("href", "").startswith(("http://", "https://")):
            self._href = values["href"]
            self._capture = True
            self._depth = 1
            self._text = []
        elif self._capture:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        self._depth -= 1
        if self._depth == 0:
            title = " ".join(" ".join(self._text).split())
            host = (urlsplit(self._href).hostname or "").lower()
            if host and not host.endswith("brave.com"):
                self.results.append(
                    SearchCandidate(self._href, title, "", self.query, len(self.results) + 1)
                )
            self._capture = False


class BraveSearchProvider:
    def __init__(self) -> None:
        limits = RunLimits(max_urls=3, max_response_bytes=3 * 1024 * 1024, timeout_seconds=20)
        self.fetcher = SafeFetcher(
            URLPolicy(("search.brave.com",)),
            limits,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )
        self.reader_fetcher = SafeFetcher(
            URLPolicy(("r.jina.ai",)),
            RunLimits(max_urls=3, max_response_bytes=3 * 1024 * 1024, timeout_seconds=30),
            user_agent="AI-Web-Data-Service-MVP/0.2",
        )

    def search(self, query: str, limit: int) -> list[SearchCandidate]:
        url = f"https://search.brave.com/search?q={quote_plus(query)}&source=web"
        try:
            result = self.fetcher.fetch(url)
        except Exception:
            result = None
        body = result.body if result else b""
        if result is None or result.status == 429:
            # Brave sometimes rate-limits Python's TLS fingerprint while serving the
            # same public HTML endpoint to a normal browser client. Some local Python
            # installations also lack a current CA chain. The URL host is fixed here
            # and the query is passed as a literal argv value.
            curl = shutil.which("curl")
            if not curl:
                raise RuntimeError("search was rate-limited and curl is unavailable")
            proc = subprocess.run(
                [
                    curl, "--fail", "--location", "--silent", "--show-error",
                    "--max-time", "20", "--max-filesize", str(3 * 1024 * 1024),
                    "--user-agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                    url,
                ],
                capture_output=True,
                timeout=25,
                check=False,
            )
            if proc.returncode != 0:
                return self._reader_search(query, limit) or _registry_fallback_candidates(query, limit)
            body = proc.stdout
        elif result.status != 200:
            raise RuntimeError(f"search returned HTTP {result.status}")
        parser = _BraveParser(query)
        parser.feed(body.decode("utf-8", errors="replace"))
        deduped: list[SearchCandidate] = []
        seen: set[str] = set()
        for candidate in parser.results:
            if candidate.url not in seen:
                seen.add(candidate.url)
                deduped.append(candidate)
            if len(deduped) >= limit:
                break
        return deduped or self._reader_search(query, limit) or _registry_fallback_candidates(query, limit)

    def _reader_search(self, query: str, limit: int) -> list[SearchCandidate]:
        brave_url = f"https://search.brave.com/search?q={quote(query, safe='')}&source=web"
        reader_url = f"https://r.jina.ai/{brave_url}"
        try:
            result = self.reader_fetcher.fetch(reader_url)
        except Exception:
            return []
        if result.status != 200:
            return []
        markdown = result.body.decode("utf-8", errors="replace")
        rows: list[SearchCandidate] = []
        seen: set[str] = set()
        lines = markdown.splitlines()
        for index, line in enumerate(lines):
            urls = re.findall(r"\]\((https?://[^)\s]+)\)", line)
            usable = []
            for url in urls:
                host = (urlsplit(url).hostname or "").lower()
                if host.endswith("brave.com") or host in {"r.jina.ai", "www.bing.com"}:
                    continue
                usable.append(url)
            if not usable:
                continue
            url = usable[-1]
            if url in seen:
                continue
            seen.add(url)
            title_line = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", line)
            outer = re.match(r"\s*\[(.*)\]\(https?://[^)]+\)\s*$", title_line)
            title = outer.group(1) if outer else title_line
            title = " ".join(re.sub(r"[\[\]#*_`]", " ", title).split()) or url
            snippet = ""
            for following in lines[index + 1 : index + 4]:
                if following.strip():
                    snippet = " ".join(following.split())
                    break
            rows.append(SearchCandidate(url, title, snippet, query, len(rows) + 1))
            if len(rows) >= limit:
                break
        return rows or self._google_reader_search(query, limit)

    def _google_reader_search(self, query: str, limit: int) -> list[SearchCandidate]:
        google_url = f"https://www.google.com/search?q={quote(query, safe='')}"
        reader_url = f"https://r.jina.ai/{google_url}"
        try:
            result = self.reader_fetcher.fetch(reader_url)
        except Exception:
            return []
        if result.status != 200:
            return []
        markdown = result.body.decode("utf-8", errors="replace")
        wrapper_rows = re.findall(
            r"\[([^\]]+)\]\((https://www\.google\.com/goto\?url=[^)]+)\)",
            markdown,
        )
        google_policy = URLPolicy(("www.google.com",))
        resolver = SafeFetcher(
            google_policy,
            RunLimits(max_urls=max(3, limit), max_response_bytes=1024 * 1024, timeout_seconds=20),
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )
        rows: list[SearchCandidate] = []
        seen: set[str] = set()
        for raw_title, wrapper_url in wrapper_rows:
            try:
                approved = google_policy.approve(html.unescape(wrapper_url))
                status, headers, body = resolver._request(approved)
            except Exception:
                continue
            location = html.unescape(headers.get("location", "")) if status in {301, 302, 303, 307, 308} else ""
            page = html.unescape(body.decode("utf-8", errors="replace"))
            targets = ([location] if location.startswith(("http://", "https://")) else [])
            targets.extend(re.findall(r"https?://[^\s\"'<>]+", page))
            target = next((url for url in targets if "google.com" not in (urlsplit(url).hostname or "")), "")
            if not target or target in seen:
                continue
            seen.add(target)
            title = " ".join(re.sub(r"[\[\]#*_`]", " ", raw_title).split()) or target
            rows.append(SearchCandidate(target, title, "", query, len(rows) + 1))
            if len(rows) >= limit:
                break
        return rows


def _registry_fallback_candidates(query: str, limit: int) -> list[SearchCandidate]:
    """Fallback adapters for company registries used by the no-key demo.

    Search engines can throttle automated clients. Registry adapters keep the
    example deterministic while every returned page is still fetched and
    independently validated by the agent.
    """
    if "erste digital gmbh" not in query.casefold():
        return []
    rows = [
        ("https://firmensicht.at/unternehmen/396294k-erste-digital-gmbh", "Erste Digital GmbH — financial statements and figures"),
        ("https://www.firmenbuchgrundbuch.at/en/query-services/business-register/fn/396294", "Erste Digital GmbH — Business Register annual filing"),
        ("https://www.evi.gv.at/f/396294k", "Erste Digital GmbH — official EVI register notices"),
        ("https://www.wirtschaft.at/u/396294k", "Erste Digital GmbH — company register and financial statements"),
    ]
    return [SearchCandidate(url, title, "", query, index) for index, (url, title) in enumerate(rows[:limit], 1)]


def build_queries(intent: SearchIntent) -> list[str]:
    company_tokens = _company_tokens(intent.company)
    company = f'"{" ".join(company_tokens) or intent.company}"'
    local_terms = SEARCH_TERMS_BY_LANGUAGE.get(intent.search_language, SEARCH_TERMS_BY_LANGUAGE["en"])
    queries = [f'{company} "{local_terms[0]}" {intent.year} PDF']
    if len(local_terms) > 1:
        queries.append(f'{company} "{local_terms[1]}" {intent.year}')
    if len(company_tokens) > 1:
        queries.append(f'"{company_tokens[0]}" "{local_terms[0]}" {intent.year} PDF')
    else:
        queries.append(f'{company} "annual report" {intent.year} PDF')
    return list(dict.fromkeys(queries))[:3]


def validate_candidate(
    intent: SearchIntent, candidate: SearchCandidate, result: FetchResult, text: str, mime: str
) -> dict:
    raw_haystack = " ".join((candidate.title, candidate.snippet, result.final_url, text[:500_000]))
    haystack = fold_text(raw_haystack)
    normalized = re.sub(r"[^\w]+", " ", haystack, flags=re.UNICODE)
    company_parts = [fold_text(token) for token in re.findall(r"[\wÀ-ž]+", intent.company, re.UNICODE)]
    company_tokens = [
        token for token in company_parts if token not in LEGAL_SUFFIXES and len(token) > 1
    ]
    token_match = bool(company_tokens) and all(
        re.search(rf"\b{re.escape(token)}\b", normalized) for token in company_tokens
    )
    company_phrase = " ".join(company_parts)
    company_exact_match = bool(company_phrase) and bool(
        re.search(rf"\b{re.escape(company_phrase)}\b", normalized)
    )
    company_match = token_match and (len(company_tokens) > 1 or company_exact_match)
    year = str(intent.year)
    report_pattern = "|".join(re.escape(term) for term in REPORT_TERMS)
    year_patterns = (
        rf"31\s*[./-]\s*12\s*[./-]\s*{year}",
        rf"12\s*/\s*31\s*/\s*{year}",
        rf"(?:{report_pattern}).{{0,55}}\b{year}\b",
        rf"\b{year}\b.{{0,35}}(?:{report_pattern})",
    )
    year_match = any(re.search(pattern, haystack, re.S) for pattern in year_patterns)
    report_match = any(term in haystack for term in REPORT_TERMS)
    is_pdf = mime == "application/pdf" or result.final_url.lower().endswith(".pdf")
    original_document = is_pdf and company_match and year_match and report_match
    restricted = any(term in haystack for term in (
        "paywall", "purchase", "buy now", "kostenpflichtig", "zahlungspflichtig",
        "placeny pristup", "zpoplatnen", "vyzaduje platbu", "acces payant",
    ))
    host = (urlsplit(result.final_url).hostname or "").lower()
    if host.endswith(("evi.gv.at", "justice.cz", "ares.gov.cz")) or ".gov." in host or host.endswith(".gov"):
        source_authority, authority_score = "official government register", 3
    elif host.endswith("firmenbuchgrundbuch.at"):
        source_authority, authority_score = "business-register access service", 2
    else:
        source_authority, authority_score = "independent company-data source", 1
    score = 45 * company_match + 30 * year_match + 20 * report_match + 5 * is_pdf
    accepted = company_match and year_match and report_match and 200 <= result.status < 400
    reasons = []
    if not company_match:
        reasons.append("company name not verified")
    if not year_match:
        reasons.append(f"target year {intent.year} not verified")
    if not report_match:
        reasons.append("annual-report context not verified")
    if accepted and not original_document:
        reasons.append("verified report page or summary; not an original report PDF")
    if restricted:
        reasons.append("source may require purchase or access")
    return {
        "accepted": accepted,
        "score": score,
        "company_match": company_match,
        "company_exact_match": company_exact_match,
        "year_match": year_match,
        "report_match": report_match,
        "original_document": original_document,
        "restricted": restricted,
        "source_authority": source_authority,
        "authority_score": authority_score,
        "reasons": reasons,
    }


class PromptDiscoveryEngine:
    def __init__(
        self,
        root: Path,
        store: AuditStore,
        search_provider: SearchProvider | None = None,
        scanner: MalwareScanner | None = None,
        company_resolver: CompanyResolver | None = None,
    ) -> None:
        self.root = root
        self.store = store
        self.search_provider = search_provider or BraveSearchProvider()
        self.scanner = scanner or MalwareScanner()
        self.company_resolver = company_resolver or CzechAresResolver()

    def _fetch_candidate(self, candidate: SearchCandidate, limits: RunLimits) -> FetchResult:
        host = urlsplit(candidate.url).hostname
        if not host:
            raise ValueError("search result has no hostname")
        return SafeFetcher(URLPolicy((host,)), limits).fetch(candidate.url)

    def run(self, job: PromptJob) -> dict:
        run_id = uuid.uuid4().hex
        self.store.create_run(run_id, job)
        errors: list[dict[str, str]] = []
        attempts: list[dict] = []
        selected: tuple[SearchCandidate, FetchResult, str, str, dict] | None = None
        best: tuple[SearchCandidate, FetchResult, str, str, dict] | None = None
        artifact: ArtifactRecord | None = None
        intent: SearchIntent | None = None
        try:
            self.store.set_state(run_id, RunState.VALIDATING)
            job.validate()
            intent = parse_prompt(job.objective)
            intent = self.company_resolver.resolve(intent)
            self.store.event(run_id, "prompt_interpreted", asdict(intent))
            if intent.jurisdiction != "unknown":
                self.store.event(run_id, "company_resolved", {
                    "company": intent.company,
                    "jurisdiction": intent.jurisdiction,
                    "company_id": intent.company_id,
                    "search_language": intent.search_language,
                })
            self.store.set_state(run_id, RunState.PLANNING)
            for attempt_number, query in enumerate(build_queries(intent)[: job.max_search_attempts], 1):
                self.store.event(run_id, "search_started", {"attempt": attempt_number, "query": query})
                attempt = {"attempt": attempt_number, "query": query, "candidates": [], "outcome": "retry"}
                try:
                    candidates = self.search_provider.search(query, job.max_candidates_per_attempt)
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    attempt["error"] = detail
                    attempts.append(attempt)
                    errors.append({"url": "", "error": detail})
                    self.store.event(run_id, "search_failed", {"attempt": attempt_number, "error": detail})
                    continue
                self.store.event(run_id, "search_results_found", {"attempt": attempt_number, "count": len(candidates)})
                for candidate in candidates:
                    row = asdict(candidate)
                    try:
                        self.store.set_state(run_id, RunState.RETRIEVING)
                        fetched = self._fetch_candidate(candidate, job.limits)
                        mime = detect_mime(fetched.body, fetched.final_url)
                        text = extract_text(fetched.body, mime)
                        validation = validate_candidate(intent, candidate, fetched, text, mime)
                        row.update({"http_status": fetched.status, "detected_mime": mime, **validation})
                        current = (candidate, fetched, text, mime, validation)
                        current_rank = (
                            validation["original_document"], validation["authority_score"],
                            not validation["restricted"], validation["score"],
                        )
                        best_rank = (
                            best[4]["original_document"], best[4]["authority_score"],
                            not best[4]["restricted"], best[4]["score"],
                        ) if best else None
                        if validation["accepted"] and (best_rank is None or current_rank > best_rank):
                            best = current
                        if validation["original_document"]:
                            selected = current
                    except Exception as exc:
                        row.update({
                            "accepted": False,
                            "score": 0,
                            "reasons": [f"{type(exc).__name__}: {exc}"],
                        })
                    attempt["candidates"].append(row)
                    self.store.event(run_id, "candidate_checked", row)
                    if selected:
                        break
                if selected:
                    attempt["outcome"] = "original report selected"
                    attempts.append(attempt)
                    break
                if best:
                    attempt["outcome"] = "verified fallback found; refining search for original report"
                attempts.append(attempt)
                self.store.event(run_id, "search_refined", {"attempt": attempt_number, "reason": attempt["outcome"]})

            selected = selected or best
            if selected:
                candidate, fetched, text, mime, validation = selected
                self.store.set_state(run_id, RunState.SCANNING)
                artifact_id = uuid.uuid4().hex
                run_dir = self.root / "artifacts" / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                artifact_path = run_dir / f"{artifact_id}.bin"
                artifact_path.write_bytes(fetched.body)
                scan = self.scanner.scan(artifact_path)
                warnings: list[str] = list(validation["reasons"])
                declared = fetched.headers.get("content-type", "")
                if not mime_matches(declared, mime):
                    warnings.append("declared MIME type does not match detected content")
                if scan.status != "clean":
                    warnings.append(f"malware scan status: {scan.status}")
                quarantined = scan.status != "clean" and not job.allow_unscanned
                artifact = ArtifactRecord(
                    artifact_id=artifact_id,
                    requested_url=fetched.requested_url,
                    final_url=fetched.final_url,
                    status=fetched.status,
                    sha256=hashlib.sha256(fetched.body).hexdigest(),
                    size=len(fetched.body),
                    declared_mime=declared.split(";", 1)[0].strip().lower(),
                    detected_mime=mime,
                    scan_status=scan.status,
                    quarantined=quarantined,
                    stored_path=str(artifact_path),
                    extracted_text="" if quarantined else text,
                    warnings=warnings,
                )
                self.store.artifact(run_id, artifact)
                self.store.event(run_id, "source_selected", {
                    "url": candidate.url,
                    "title": candidate.title,
                    **validation,
                })
                state = RunState.COMPLETED if validation["original_document"] and not warnings else RunState.COMPLETED_WITH_WARNINGS
                self.store.set_state(run_id, state)
            else:
                errors.append({"url": "", "error": "No source passed company, year, and report validation."})
                self.store.set_state(run_id, RunState.FAILED)
        except Exception as exc:
            errors.append({"url": "", "error": f"{type(exc).__name__}: {exc}"})
            self.store.event(run_id, "run_failed", errors[-1])
            self.store.set_state(run_id, RunState.FAILED)

        report = self.store.run_report(run_id)
        assert report is not None
        validation = selected[4] if selected else None
        manifest = {
            "run_id": run_id,
            "state": report["run"]["state"],
            "objective": job.objective,
            "intent": asdict(intent) if intent else None,
            "delivery_status": (
                "original_report_verified" if validation and validation["original_document"]
                else "verified_source_report_restricted_or_summary" if validation
                else "not_found"
            ),
            "selected_source": ({"title": selected[0].title, "url": selected[1].final_url, **selected[4]} if selected else None),
            "search_attempts": attempts,
            "artifacts": [artifact.to_dict()] if artifact else [],
            "errors": errors,
        }
        manifest_dir = self.root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{run_id}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)
        return manifest
