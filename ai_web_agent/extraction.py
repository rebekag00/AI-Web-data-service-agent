from __future__ import annotations

import html
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path


_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def _extract_pdf_with_bundled_runtime(body: bytes, max_chars: int) -> str:
    project_root = Path(__file__).resolve().parent.parent
    helper = project_root / "tools" / "extract_pdf_report.py"
    configured = os.environ.get("AI_WEB_AGENT_PDF_PYTHON")
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    candidates = [Path(configured)] if configured else []
    candidates.extend([bundled, Path(sys.executable)])
    for python in candidates:
        if not python.is_file():
            continue
        try:
            proc = subprocess.run(
                [str(python), str(helper), "-"],
                input=body,
                cwd=project_root,
                capture_output=True,
                timeout=45,
                check=False,
            )
            if proc.returncode != 0:
                continue
            parsed = json.loads(proc.stdout.decode("utf-8"))
            text = "\n\n".join(page.get("text", "") for page in parsed.get("pages", []))
            if text.strip():
                return text[:max_chars]
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return ""


def extract_text(body: bytes, mime: str, max_chars: int = 500_000) -> str:
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(body))
            pages: list[str] = []
            for page in reader.pages[:50]:
                pages.append(page.extract_text() or "")
                if sum(len(value) for value in pages) >= max_chars:
                    break
            return "\n\n".join(pages)[:max_chars]
        except Exception:
            return _extract_pdf_with_bundled_runtime(body, max_chars)
    if mime == "application/json":
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            return json.dumps(parsed, ensure_ascii=False, indent=2)[:max_chars]
        except json.JSONDecodeError:
            return ""
    if mime in {"text/html", "application/xhtml+xml"}:
        text = body.decode("utf-8", errors="replace")
        text = _SCRIPT_STYLE.sub(" ", text)
        text = _TAGS.sub("\n", text)
        text = html.unescape(text)
        text = _SPACE.sub(" ", text)
        return _BLANKS.sub("\n\n", text).strip()[:max_chars]
    if mime.startswith("text/") or mime in {"application/xml", "text/xml"}:
        return body.decode("utf-8", errors="replace")[:max_chars]
    return ""
