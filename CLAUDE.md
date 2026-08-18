# CLAUDE.md — working conventions for this repo

Context for any AI assistant (and humans) working on this project.

## What this is
An AI-powered on-call assistant for the Loyalty platform. It ingests one Slack
channel's history into a structured, searchable knowledge base and uses RAG to
surface likely solutions for new incidents — both on demand and as proactive,
confidence-gated auto-posts. Full design in `docs/design-v2.md`.

## Project layout
- `src/oncall/ingest/`    Slack export + normalization (write path, step 1–2)
- `src/oncall/extract/`   Bedrock extraction into structured cases (step 3);
                          `parsing.py` is shared with the live path
- `src/oncall/eval/`      validation report (step 4) + `holdout.py` retrieval
                          evaluation (the §5 go/no-go, `make holdout`)
- `src/oncall/retrieval/` answer prompt + CLI (read path, local RAG — built)
- `src/oncall/lambdas/`   **deployed live track**: `post_events.py` (Events API
                          ingestion → S3 → extraction on resolution → KB sync),
                          `questions.py` (@-mention → KB answer with permalink
                          citations), `live_extract.py`, `slack_verify.py`.
                          See its README for deployment/env details.
- `src/oncall/bot/`       trigger classifier + confidence gate for auto-post (Phase 1)
- `src/oncall/prompts.py` all LLM prompts live here, versioned
- `infra/terraform/`      import flow for the existing Lambda (see its README);
                          S3 + Bedrock KB on S3 Vectors + DynamoDB still to build
- `tests/`                pytest; `docs/`                design + prompt specs
- `data/`                 gitignored local artifacts

## How to build and run
```bash
make install     # editable install + dev tools
make test        # pytest
make lint        # ruff
make pipeline    # normalize -> extract (30-thread sample) -> validate
make export CHANNEL=C0XXXXXXX   # Slack export (needs SLACK_BOT_TOKEN)
make holdout     # held-out retrieval eval -> hit-rate vs the 60% exit bar
```
Config via env vars (`.env.example`): `SLACK_BOT_TOKEN`, `AWS_REGION`,
`BEDROCK_MODEL_ID`.

## Conventions (please follow)
- **Never hardcode or commit secrets.** Tokens come from env / a secret manager.
- **Fix extraction quality in the prompt (`prompts.py`), not by hand-editing data.**
  Raw threads on disk are the source of truth; structured cases are derived and
  re-runnable.
- **Extraction runs one thread per call at temperature 0** for deterministic output.
- **Don't index low-signal cases**: only `is_resolved` and `confidence >= 0.4`
  (cutoff is tunable — see the validation report).
- **`make test && make lint` must pass before committing.**
- New prompts go in `prompts.py`; new pipeline stages get their own subpackage
  plus a test.
- **The Knowledge Base indexes only `cases/` (extracted, redacted, gated).**
  Raw thread docs under `events/` are audit-only and must never be indexed.
- Lambda modules import helpers with a flat-zip fallback (`try: from
  oncall... except ImportError`) — keep that pattern so both the package and
  the deployed zip work.

## Roadmap pointer
PoC (steps 1–4, here now) → MVP (live ingest + bot + shadow auto-post) →
go-live/harden → evolve (Datadog/ArgoCD, AgentCore). See `docs/design-v2.md` §8.
