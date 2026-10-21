from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .storage import AuditStore


class ExcelExportError(RuntimeError):
    pass


def _pdf_content(project_root: Path, manifest: dict) -> dict:
    artifacts = manifest.get("artifacts") or []
    artifact = artifacts[0] if artifacts else {}
    if artifact.get("detected_mime") != "application/pdf" or artifact.get("quarantined"):
        return {}
    source = Path(str(artifact.get("stored_path") or ""))
    if not source.is_file():
        return {}
    configured = os.environ.get("AI_WEB_AGENT_PDF_PYTHON")
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    candidates = [Path(configured)] if configured else []
    candidates.extend([bundled, Path(sys.executable)])
    helper = project_root / "tools" / "extract_pdf_report.py"
    for python in candidates:
        if not python.is_file():
            continue
        proc = subprocess.run(
            [str(python), str(helper), str(source)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                continue
    return {}


def export_run_excel(
    run_id: str,
    manifest: dict,
    store: AuditStore,
    data_dir: Path,
) -> Path:
    report = store.run_report(run_id)
    if report is None:
        raise ExcelExportError(f"unknown run id: {run_id}")

    node = shutil.which("node")
    if not node:
        raise ExcelExportError("Node.js is required for Excel export")

    project_root = Path(__file__).resolve().parent.parent
    exporter = project_root / "tools" / "export_results.mjs"
    input_dir = data_dir / "export_inputs"
    output_dir = data_dir / "exports"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / f"{run_id}.json"
    output_path = output_dir / f"{run_id}.xlsx"
    payload = {"manifest": manifest, "audit": report, "pdf_content": _pdf_content(project_root, manifest)}
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [node, str(exporter), str(input_path), str(output_path)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 or not output_path.exists():
        message = proc.stderr.strip() or proc.stdout.strip() or "unknown export failure"
        raise ExcelExportError(f"Excel export failed: {message}")
    return output_path
