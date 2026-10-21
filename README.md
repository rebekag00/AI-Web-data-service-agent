# AI Web Data Service MVP

This repository contains an offline-first implementation of the first vertical
slice of the AI Web Data Service. It does not require an API key or an external
LLM. The web app accepts one natural-language prompt, discovers candidate web
sources, validates the company/year/report context, retries with refined search
terms, and exports the result and evidence to Excel.

Prompts and source documents can use English, Czech, German, Slovak, Polish,
French, Spanish, Italian, Dutch, Hungarian, or Romanian annual-report terms.
Accents are normalized for validation while native-language queries are kept.

## Included

- natural-language company/report/year parsing
- no-key public-web discovery with bounded retry queries
- candidate ranking and company/year/report validation
- public-IP, protocol, port, hostname, and domain policy checks
- redirect-by-redirect validation to reduce SSRF risk
- size-limited HTTP and HTTPS retrieval
- SHA-256 hashing, MIME validation, and quarantine decisions
- optional ClamAV integration through `clamscan`
- safe extraction for HTML, JSON, XML, CSV, text, and retrieved PDFs
- SQLite run, event, and artifact audit records
- a deterministic agent state machine and JSON run manifest
- standard-library unit tests

This is an MVP, not a production security boundary. Production deployment also
needs controlled egress, DNS enforcement, container isolation, enterprise
malware scanning, identity/RBAC, a secret manager, DLP, and legal source
approval.

## Run locally

Use the bundled or system Python 3.11+:

```bash
python -m unittest discover -s tests -v
python -m ai_web_agent run \
  --objective "Retrieve the public example page" \
  --url https://example.com/ \
  --allow-domain example.com
```

## Use the web page

```bash
python -m ai_web_agent serve
```

Open `http://127.0.0.1:8765` (do not open `index.html` as a `file://` page), then
enter a prompt such as:

> I want to see the annual report for ERSTE DIGITAL GMBH 2025

The page shows the interpreted request, every search attempt, the validation
checks, the selected source, and an Excel download. When the selected source is
an original PDF, the workbook contains:

- `Overview` with verification and headline metrics
- `Financial statements` with structured rows extracted from the PDF
- `PDF content` with the text of every extracted PDF page
- `Search attempts` and `Audit` evidence tabs

The local Excel exporter uses the bundled `@oai/artifact-tool` runtime. In this
workspace, `node_modules` is linked to that runtime and remains untracked.

By default, content is quarantined if no malware scanner is available. For a
local demonstration with a trusted text page, add `--allow-unscanned`. Never
use that option as a production policy.

Runtime data is written under `var/`:

- `var/audit.sqlite3` contains runs, events, and artifact records.
- `var/artifacts/<run-id>/` contains retrieved response bodies.
- `var/manifests/<run-id>.json` contains the final run manifest.
- `var/exports/<run-id>.xlsx` contains the downloadable Excel report.

## Inspect a run

```bash
python -m ai_web_agent show RUN_ID
```

## Current boundaries

The no-key search provider can be throttled by public search engines, so the
ERSTE DIGITAL demonstration includes public Austrian registry adapters as a
deterministic fallback. Authenticated/paywalled report retrieval, scheduled
runs, and production-grade browser automation remain future extensions.
