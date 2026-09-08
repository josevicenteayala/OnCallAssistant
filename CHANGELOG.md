# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Documentation reconciled with what shipped in 0.2.0: the roadmap no longer
  describes the backfill uploader as unwritten, `src/oncall/publish/` appears in
  the project layout and README structure, the docs index lists the client
  infrastructure guide, and the release/tagging rules are referenced from
  `CLAUDE.md`.

## [0.2.0] — 2026-09-08

Back-fill tooling and client-facing documentation. No changes to the deployed
Lambda runtime behaviour.

### Added
- **Backfill uploader** (`oncall.publish.upload_cases`, `make upload`): splits
  `structured_cases.jsonl` into one S3 object per indexable case, keyed
  `cases/{channel_id}/{thread_ts}.json` — the same layout the live path writes,
  so a thread later re-resolved in Slack updates its case instead of creating a
  duplicate. Applies the same `is_resolved` + confidence gate as the index step,
  derives the channel from each case's permalink with a `--channel` fallback, and
  offers `--dry-run` and `--sync` (starts a Knowledge Base ingestion job).
- **Client infrastructure recreation guide** (`docs/OnCallAssistant-Infrastructure-Guide.pdf`,
  22 pages, with regenerable source in `docs/infrastructure-guide/`): resource
  inventory, least-privilege IAM for both roles, Slack app setup, deployment
  order, an end-to-end smoke test with expected log lines, troubleshooting,
  security posture, cost model, and the designed-but-not-built gaps. All
  environment-specific values are placeholders.
- `docs/README.md` index, and backfill upload variables in `.env.example`.

### Changed
- Documentation brought in line with the deployed system: `design-v2.md` now
  opens with an as-built status table separating what is deployed from what
  remains designed-only; `data-pipeline.md` was rewritten for the current make
  targets and the full six-step backfill; `extraction-prompt.md` points at
  `prompts.py` as the executable source of truth.
- Ingestion and query diagrams corrected — the ingestion flow now shows the
  resolution-only extraction band and the `events/` (audit) versus `cases/`
  (indexed) split, and the query flow shows the thread-context fetch and
  citation rebuild.

## [0.1.0] — 2026-08-21

First tagged release. A Slack-native retrieval-augmented on-call assistant,
verified end-to-end in production: incident thread → extraction → Knowledge
Base → cited answer in Slack.

### Added
- **Live ingestion Lambda** (`post_events`): Slack Events API → per-thread JSON
  documents in the S3 `events/` prefix, keyed on `thread_ts`, with keyword role
  classification (alert / investigation / action / resolution).
- **Extraction on resolution** (`live_extract`): when a threaded reply signals a
  resolution, the whole thread is distilled through the shared extraction prompt
  — resolution judgment, secret and PII redaction, confidence score — and only
  cases clearing the confidence gate are written to the `cases/` prefix, which is
  the sole Knowledge Base data source.
- **On-demand bot Lambda** (`questions`): `@`-mention → Bedrock
  `retrieve_and_generate` over the Knowledge Base → grounded, threaded reply.
  Answers are drawn only from retrieved cases and say so plainly when no
  precedent exists.
- **Held-out retrieval evaluation** (`oncall.eval.holdout`, `make holdout`):
  hides recent resolved incidents, queries with symptom text only, and reports a
  hit-rate against the agreed 60% go/no-go bar, with an HTML report for auditing
  the judge's verdicts.
- **Console-friendly deployment** (`make lambda_zips`): builds per-function zips
  whose entry file is named `lambda_function.py`, so the console handler setting
  never changes between releases.
- Terraform import workflow for capturing the existing Lambda and Function URL
  into state, and the extraction/validation batch pipeline with an HTML
  validation report.

### Changed
- Lambda handlers consolidated into an importable, unit-tested package with
  shared Slack HMAC verification and lazy boto3 clients, replacing byte-identical
  duplicated files that could not be imported by the test suite.
- Structured, step-indexed logging across both handlers with propagated request
  IDs, per-step durations, and full exception traces.

### Fixed
- **Citations are rebuilt in code** rather than trusted from the model, which was
  observed emitting internal citation markers, and whose retrieved chunks could
  arrive split before the permalink field or with no chunk text at all. Source
  links are now the deduplicated union of the citation's S3 key, chunk contents,
  and any URLs the model transcribed.
- Slack retry deliveries are dropped by both handlers; Slack redelivers events
  unacknowledged within ~3 seconds, and neither the timeline append nor the
  answer post is idempotent.
- `parse_case` now guarantees a dict or `None`, so a model returning a JSON list
  or bare string degrades cleanly instead of raising.

### Security
- Live ingestion routed through the extraction pipeline, so the Knowledge Base
  indexes only redacted, confidence-gated cases. Previously every raw message was
  indexed, making anything pasted into the channel — credentials included —
  retrievable through the bot. The `events/` prefix remains an audit trail and
  must never be configured as a data source.

[Unreleased]: https://github.com/josevicenteayala/OnCallAssistant/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/josevicenteayala/OnCallAssistant/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/josevicenteayala/OnCallAssistant/releases/tag/v0.1.0
