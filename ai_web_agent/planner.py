from __future__ import annotations

from dataclasses import dataclass

from .models import CollectionJob


@dataclass(frozen=True)
class FetchAction:
    url: str
    rationale: str


class DeterministicPlanner:
    """A transparent no-LLM planner for known-URL acquisition jobs."""

    def plan(self, job: CollectionJob) -> list[FetchAction]:
        seen: set[str] = set()
        actions: list[FetchAction] = []
        for url in job.urls:
            normalized = url.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                actions.append(
                    FetchAction(
                        url=normalized,
                        rationale=f"URL explicitly supplied for objective: {job.objective}",
                    )
                )
        return actions

