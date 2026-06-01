# On-Call Assistant

AI-powered on-call assistant for the Loyalty platform. It turns three years of one
Slack channel into a structured, searchable knowledge base and uses RAG to surface
likely solutions for new incidents — answering engineers on demand and posting
confidence-gated suggestions into new issue threads.

> Full design: [`docs/design-v2.md`](docs/design-v2.md) · extraction prompt spec:
> [`docs/extraction-prompt.md`](docs/extraction-prompt.md)

## Structure

```
src/oncall/
  ingest/      slack_export.py, normalize.py     write path: steps 1–2
  extract/     extract.py (Bedrock)              write path: step 3
  eval/        validate.py (HTML report)         step 4 + later retrieval eval
  retrieval/   answer prompt + CLI               next slice (read path)
  bot/         Slack bot, classifier, gate       Phase 1
  prompts.py   all LLM prompts, versioned in one place
infra/terraform/  S3 + Bedrock KB on S3 Vectors + DynamoDB   next slice
tests/   pytest    docs/   design & prompt specs    data/   local artifacts (gitignored)
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
make install                 # editable install + dev tools
make test                    # runs today — no cloud access needed

cp .env.example .env         # then fill in your values
# export SLACK_BOT_TOKEN / AWS_REGION / BEDROCK_MODEL_ID

make export CHANNEL=C0XXXXXXX   # Slack export (read-only)
make pipeline                   # normalize -> extract (30 sample) -> validate
open data/validation_report.html
```

When the 30-thread sample looks right, run the full extract:
`make extract LIMIT=0` then `make validate`.

Once cases look good, try the read path locally (no Knowledge Base needed yet):

```bash
make index                                   # embed cases -> data/index.json
make ask Q="pods crashlooping after a deploy"
```

See [`docs/data-pipeline.md`](docs/data-pipeline.md) for what to look for in the
validation report and how to tune the confidence cutoff.

## Where this sits in the plan

PoC (steps 1–4, **this repo today**) → MVP (live ingestion, Slack bot, shadow-mode
auto-post) → go-live & harden → evolve (Datadog/ArgoCD link-outs, AgentCore). The
phased roadmap with exit criteria is in `docs/design-v2.md` §8.

## Conventions

Secrets only via env / a secret manager — never committed. Fix extraction quality
in `prompts.py`, never by hand-editing data. `make test && make lint` before
committing. More in [`CLAUDE.md`](CLAUDE.md).
