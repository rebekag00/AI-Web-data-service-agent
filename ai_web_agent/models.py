from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    SCANNING = "scanning"
    EXTRACTING = "extracting"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


@dataclass(frozen=True)
class RunLimits:
    max_urls: int = 100
    max_redirects: int = 5
    max_response_bytes: int = 20 * 1024 * 1024
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class CollectionJob:
    objective: str
    urls: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    requested_by: str = "local-user"
    allow_unscanned: bool = False
    limits: RunLimits = field(default_factory=RunLimits)

    def validate(self) -> None:
        if not self.objective.strip():
            raise ValueError("objective is required")
        if not self.urls:
            raise ValueError("at least one URL is required")
        if not self.allowed_domains:
            raise ValueError("at least one allowed domain is required")
        if len(self.urls) > self.limits.max_urls:
            raise ValueError("URL count exceeds max_urls")


@dataclass(frozen=True)
class PromptJob:
    objective: str
    requested_by: str = "local-web-user"
    max_search_attempts: int = 3
    max_candidates_per_attempt: int = 8
    allow_unscanned: bool = True
    limits: RunLimits = field(
        default_factory=lambda: RunLimits(
            max_urls=15,
            max_redirects=5,
            max_response_bytes=30 * 1024 * 1024,
            timeout_seconds=30,
        )
    )

    def validate(self) -> None:
        if not self.objective.strip():
            raise ValueError("prompt is required")
        if len(self.objective) > 2_000:
            raise ValueError("prompt is too long")


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    resolved_ip: str
    redirects: tuple[str, ...] = ()


@dataclass
class ArtifactRecord:
    artifact_id: str
    requested_url: str
    final_url: str
    status: int
    sha256: str
    size: int
    declared_mime: str
    detected_mime: str
    scan_status: str
    quarantined: bool
    stored_path: str
    extracted_text: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
