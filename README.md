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
  extract/     extract.py, parsing.py (Bedrock)  write path: step 3
  eval/        validate.py, holdout.py           step 4 + §5 go/no-go eval
  retrieval/   answer prompt + CLI               read path (local RAG — built)
  lambdas/     deployed live track: Events-API ingestion + @-mention bot
  bot/         trigger classifier, gate          Phase 1
  prompts.py   all LLM prompts, versioned in one place
infra/terraform/  import flow for the live Lambda; KB/DynamoDB modules next
tests/   pytest    docs/   design & prompt specs    data/   local artifacts (gitignored)
```

The live track (`src/oncall/lambdas/`) is **deployed and verified end-to-end**
(2026-08-21) behind Lambda Function URLs: message events accumulate thread
docs in S3 (`events/`, audit-only), a resolution signal triggers the shared
extraction prompt, and only redacted, confidence-gated cases land in `cases/`
— the only prefix the Bedrock Knowledge Base indexes. The @-mention bot
answers from the KB with deterministic Slack permalink citations. Deployment,
env vars, IAM, and a troubleshooting table: `src/oncall/lambdas/README.md`
(the ops runbook).

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
make holdout                                 # §5 go/no-go: hit-rate on held-out incidents
open data/holdout_report.html
```

See [`docs/data-pipeline.md`](docs/data-pipeline.md) for what to look for in the
validation report and how to tune the confidence cutoff.

## Backfill the knowledge base (next milestone)

The live loop only learns from threads resolved after deployment; the backfill
loads the channel's history so the bot is useful from day one:

1. `make export CHANNEL=C0XXXXXXX` — one-time, read-only Slack export.
2. `make pipeline` — normalize, extract a **30-thread sample**, and open
   `data/validation_report.html`; tune the prompt/cutoff before spending on
   the full corpus.
3. `make extract LIMIT=0 && make validate` — full extraction.
4. Upload the indexable cases to `s3://<bucket>/cases/` as one JSON object per
   case, keyed `{channel_id}/{thread_ts}.json` to match the live path's
   layout (small uploader script — not written yet), then **Sync** the KB
   data source.
5. `make index && make holdout` — hit-rate on held-out incidents vs the
   **60% go/no-go bar** (`data/holdout_report.html`). This number is the
   evidence for rolling out to the real on-call team.

Before backfilling a real channel, delete any test cases from `cases/` and
re-sync so experiment chatter doesn't pollute retrieval.

## Where this sits in the plan

PoC (steps 1–4: built) → **MVP (in progress: live ingestion + extraction +
on-demand bot deployed and verified; remaining: backfill + retrieval eval,
trigger classifier + shadow-mode auto-post, 👍/👎 capture, kill switch)** →
go-live & harden (Terraform full stack, Secrets Manager, cost alerts) →
evolve (Datadog/ArgoCD link-outs, AgentCore). The phased roadmap with exit
criteria is in `docs/design-v2.md` §8.

## Conventions

Secrets only via env / a secret manager — never committed. Fix extraction quality
in `prompts.py`, never by hand-editing data. `make test && make lint` before
committing. More in [`CLAUDE.md`](CLAUDE.md).
