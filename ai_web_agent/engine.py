from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Callable

from .extraction import extract_text
from .fetcher import SafeFetcher
from .models import ArtifactRecord, CollectionJob, RunState
from .planner import DeterministicPlanner
from .security import MalwareScanner, detect_mime, mime_matches
from .storage import AuditStore


class CollectionEngine:
    def __init__(
        self,
        root: Path,
        store: AuditStore,
        fetcher_factory: Callable[[CollectionJob], SafeFetcher],
        planner: DeterministicPlanner | None = None,
        scanner: MalwareScanner | None = None,
    ) -> None:
        self.root = root
        self.store = store
        self.fetcher_factory = fetcher_factory
        self.planner = planner or DeterministicPlanner()
        self.scanner = scanner or MalwareScanner()

    def run(self, job: CollectionJob) -> dict:
        run_id = uuid.uuid4().hex
        self.store.create_run(run_id, job)
        records: list[ArtifactRecord] = []
        errors: list[dict[str, str]] = []
        try:
            self.store.set_state(run_id, RunState.VALIDATING)
            job.validate()
            self.store.set_state(run_id, RunState.PLANNING)
            actions = self.planner.plan(job)
            self.store.event(run_id, "plan_created", {"action_count": len(actions)})
            fetcher = self.fetcher_factory(job)

            for action in actions:
                try:
                    self.store.set_state(run_id, RunState.RETRIEVING)
                    self.store.event(
                        run_id,
                        "fetch_started",
                        {"url": action.url, "rationale": action.rationale},
                    )
                    result = fetcher.fetch(action.url)
                    artifact_id = uuid.uuid4().hex
                    run_dir = self.root / "artifacts" / run_id
                    run_dir.mkdir(parents=True, exist_ok=True)
                    artifact_path = run_dir / f"{artifact_id}.bin"
                    artifact_path.write_bytes(result.body)

                    self.store.set_state(run_id, RunState.SCANNING)
                    detected = detect_mime(result.body, result.final_url)
                    declared = result.headers.get("content-type", "")
                    scan = self.scanner.scan(artifact_path)
                    warnings: list[str] = []
                    if not mime_matches(declared, detected):
                        warnings.append("declared MIME type does not match detected content")
                    quarantined = scan.status != "clean" and not job.allow_unscanned
                    if scan.status != "clean":
                        warnings.append(f"malware scan status: {scan.status}")

                    text = ""
                    if not quarantined and scan.status != "infected":
                        self.store.set_state(run_id, RunState.EXTRACTING)
                        text = extract_text(result.body, detected)

                    record = ArtifactRecord(
                        artifact_id=artifact_id,
                        requested_url=result.requested_url,
                        final_url=result.final_url,
                        status=result.status,
                        sha256=hashlib.sha256(result.body).hexdigest(),
                        size=len(result.body),
                        declared_mime=declared.split(";", 1)[0].strip().lower(),
                        detected_mime=detected,
                        scan_status=scan.status,
                        quarantined=quarantined,
                        stored_path=str(artifact_path),
                        extracted_text=text,
                        warnings=warnings,
                    )
                    self.store.artifact(run_id, record)
                    self.store.event(
                        run_id,
                        "fetch_completed",
                        {
                            "url": result.final_url,
                            "status": result.status,
                            "resolved_ip": result.resolved_ip,
                            "redirects": list(result.redirects),
                            "artifact_id": artifact_id,
                            "quarantined": quarantined,
                        },
                    )
                    records.append(record)
                except Exception as exc:
                    error = {"url": action.url, "error": f"{type(exc).__name__}: {exc}"}
                    errors.append(error)
                    self.store.event(run_id, "fetch_failed", error)

            final_state = (
                RunState.COMPLETED_WITH_WARNINGS if errors or any(r.warnings for r in records)
                else RunState.COMPLETED
            )
            if not records and errors:
                final_state = RunState.FAILED
            self.store.set_state(run_id, final_state)
        except Exception as exc:
            errors.append({"url": "", "error": f"{type(exc).__name__}: {exc}"})
            self.store.event(run_id, "run_failed", errors[-1])
            self.store.set_state(run_id, RunState.FAILED)

        report = self.store.run_report(run_id)
        assert report is not None
        manifest = {
            "run_id": run_id,
            "state": report["run"]["state"],
            "objective": job.objective,
            "artifacts": [record.to_dict() for record in records],
            "errors": errors,
        }
        manifest_dir = self.root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{run_id}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)
        return manifest

