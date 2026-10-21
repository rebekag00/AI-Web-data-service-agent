from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import CollectionEngine
from .fetcher import SafeFetcher
from .models import CollectionJob, RunLimits
from .policy import URLPolicy
from .storage import AuditStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-web-agent")
    parser.add_argument("--data-dir", type=Path, default=Path("var"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run an audited known-URL collection job")
    run.add_argument("--objective", required=True)
    run.add_argument("--url", action="append", required=True, dest="urls")
    run.add_argument("--allow-domain", action="append", required=True, dest="domains")
    run.add_argument("--requested-by", default="local-user")
    run.add_argument("--allow-unscanned", action="store_true")
    run.add_argument("--max-urls", type=int, default=100)
    run.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)
    run.add_argument("--timeout", type=float, default=20.0)

    show = subparsers.add_parser("show", help="show the complete audit report for a run")
    show.add_argument("run_id")

    serve = subparsers.add_parser("serve", help="start the local web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = AuditStore(args.data_dir / "audit.sqlite3")

    if args.command == "serve":
        from .web import serve

        serve(args.host, args.port, args.data_dir)
        return 0

    if args.command == "show":
        report = store.run_report(args.run_id)
        if report is None:
            parser.error(f"unknown run id: {args.run_id}")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    limits = RunLimits(
        max_urls=args.max_urls,
        max_response_bytes=args.max_bytes,
        timeout_seconds=args.timeout,
    )
    job = CollectionJob(
        objective=args.objective,
        urls=tuple(args.urls),
        allowed_domains=tuple(args.domains),
        requested_by=args.requested_by,
        allow_unscanned=args.allow_unscanned,
        limits=limits,
    )

    def make_fetcher(current_job: CollectionJob) -> SafeFetcher:
        return SafeFetcher(URLPolicy(current_job.allowed_domains), current_job.limits)

    engine = CollectionEngine(args.data_dir, store, make_fetcher)
    manifest = engine.run(job)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["state"] != "failed" else 1
