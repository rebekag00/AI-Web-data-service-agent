from __future__ import annotations

import mimetypes
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def detect_mime(body: bytes, url: str) -> str:
    prefix = body[:512].lstrip()
    lower = prefix.lower()
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if body.startswith(b"PK\x03\x04"):
        return "application/zip"
    if lower.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if lower.startswith((b"<?xml", b"<rss", b"<feed")):
        return "application/xml"
    if prefix.startswith((b"{", b"[")):
        return "application/json"
    guessed, _ = mimetypes.guess_type(url)
    if guessed and guessed.startswith("text/"):
        return guessed
    try:
        prefix.decode("utf-8")
        return guessed or "text/plain"
    except UnicodeDecodeError:
        return guessed or "application/octet-stream"


def mime_matches(declared: str, detected: str) -> bool:
    if not declared:
        return True
    declared = declared.split(";", 1)[0].strip().lower()
    if declared in {"application/octet-stream", "binary/octet-stream"}:
        return True
    aliases = {
        "text/xml": "application/xml",
        "application/xhtml+xml": "text/html",
    }
    return aliases.get(declared, declared) == aliases.get(detected, detected)


@dataclass(frozen=True)
class ScanResult:
    status: str
    detail: str


class MalwareScanner:
    def scan(self, path: Path) -> ScanResult:
        executable = shutil.which("clamscan")
        if not executable:
            return ScanResult("not_configured", "clamscan is unavailable")
        proc = subprocess.run(
            [executable, "--no-summary", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            return ScanResult("clean", "ClamAV reported clean")
        if proc.returncode == 1:
            return ScanResult("infected", "ClamAV detected malware")
        return ScanResult("error", "ClamAV could not complete the scan")

