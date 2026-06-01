# CLAUDE.md — working conventions for this repo

Context for any AI assistant (and humans) working on this project.

## What this is
An AI-powered on-call assistant for the Loyalty platform. It ingests one Slack
channel's history into a structured, searchable knowledge base and uses RAG to
surface likely solutions for new incidents — both on demand and as proactive,
confidence-gated auto-posts. Full design in `docs/design-v2.md`.

## Project layout
- `src/oncall/ingest/`    Slack export + normalization (write path, step 1–2)
- `src/oncall/extract/`   Bedrock extraction into structured cases (step 3)
- `src/oncall/eval/`      validation report + (later) retrieval evaluation (step 4)
- `src/oncall/retrieval/` answer prompt + CLI (read path, local RAG — built)
- `src/oncall/bot/`       Slack bot, trigger classifier, confidence gate (Phase 1)
- `src/oncall/prompts.py` all LLM prompts live here, versioned
- `infra/terraform/`      S3 + Bedrock KB on S3 Vectors + DynamoDB (next slice)
- `tests/`                pytest; `docs/`                design + prompt specs
- `data/`                 gitignored local artifacts

## How to build and run
```bash
make install     # editable install + dev tools
make test        # pytest
make lint        # ruff
make pipeline    # normalize -> extract (30-thread sample) -> validate
make export CHANNEL=C0XXXXXXX   # Slack export (needs SLACK_BOT_TOKEN)
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

## Roadmap pointer
PoC (steps 1–4, here now) → MVP (live ingest + bot + shadow auto-post) →
go-live/harden → evolve (Datadog/ArgoCD, AgentCore). See `docs/design-v2.md` §8.
