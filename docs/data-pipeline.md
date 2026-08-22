# On-Call Assistant — Data Pipeline (backfill)

This is the **batch** path: turn a Slack channel's history into structured,
quality-checked incident cases, publish them to the Knowledge Base, and measure
whether retrieval is actually useful.

The **live** path (deployed Lambdas) does the same work per-thread as incidents
resolve — see [`../src/oncall/lambdas/README.md`](../src/oncall/lambdas/README.md).
Both paths share one extraction prompt and one confidence gate, and write the
same S3 key layout, so backfilled and live cases are indistinguishable to the
Knowledge Base.

```
export  →  normalize  →  extract  →  validate  →  upload  →  index + holdout
(Slack)    (local)      (Bedrock)   (HTML report) (S3 + KB)  (go/no-go number)
```

## What you do vs. what's automated

**You (one-time setup, credentials):**
- Create a Slack app, install it to the workspace, grant the bot scopes
  `channels:history`, `channels:read`, `users:read` (add `groups:*` for a
  private channel). Get the channel ID (Slack: channel → View details).
- Enable **Amazon Bedrock model access** for a Converse-capable generation
  model plus Titan embeddings, and have AWS credentials available locally.
- Keep tokens in `.env` (gitignored) or a secret manager — never in the repo.

**Automated (these commands):** export, clean, extract, report, publish, evaluate.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env      # then fill it in
set -a && . ./.env && set +a
```

`.env` keys: `SLACK_BOT_TOKEN`, `AWS_REGION`, `BEDROCK_MODEL_ID`,
`EMBED_MODEL_ID`, and for the upload step `S3_BUCKET_NAME`, `BEDROCK_KB_ID`,
`BEDROCK_DATA_SOURCE_ID`.

> `BEDROCK_MODEL_ID` is intentionally not hardcoded: the right value depends on
> the model and region you enable, and Bedrock often expects a region-specific
> inference-profile id. Use whatever appears in the Bedrock console for the
> model you turned on — a **generation** model (e.g. a Claude or Nova id), never
> an embeddings model.

## Run order

```bash
# 1. Export (reads Slack only, with rate-limit backoff)
make export CHANNEL=C0XXXXXXX

# 2-4. Normalize -> extract a 30-thread SAMPLE -> validation report
make pipeline
open data/validation_report.html
```

**Stop here and read the report** before spending on the full corpus. When the
sample looks right:

```bash
# 3b. Full extraction (one Bedrock call per thread)
make extract LIMIT=0 && make validate

# 5. Publish indexable cases to the KB prefix and start a sync
python -m oncall.publish.upload_cases --cases ./data/structured_cases.jsonl \
    --bucket $S3_BUCKET_NAME --dry-run      # preview first
make upload BUCKET=$S3_BUCKET_NAME

# 6. Local retrieval + the go/no-go number
make index
make ask Q="pods crashlooping after a deploy"
make holdout
open data/holdout_report.html
```

## What to look for in the validation report

The report sorts cases by confidence and flags each **OK** (would be indexed:
resolved and confidence ≥ 0.4) or **DROP** (held for review). Check:

- **Are the OK rows real fixes?** If a high-confidence row has a vague or wrong
  `solution`, the prompt needs tightening (in `prompts.py`) — re-run, don't patch
  data by hand.
- **Are good DROP rows being lost?** If useful resolutions sit just under 0.4, the
  cutoff is too high. This is how you pick the real threshold; it is a flag on
  `make index`, `make upload`, and the `CONFIDENCE_CUTOFF` env var on the live
  Lambda — **keep all three in agreement.**
- **Do categories match how your team talks?** Adjust the controlled vocabulary
  and the category definition in the prompt if the mapping feels off.
- **Did redaction fire where it should?** Spot-check any row with `redaction_applied`
  true, and scan a few false ones for missed secrets.
- **Coverage:** what fraction of threads end up indexable? That number is an early
  read on whether the channel is rich enough to be worth the full build.

## What to look for in the holdout report

`make holdout` hides the N most recent indexable cases, asks the retriever about
each one's *symptom text only*, and has an LLM judge decide whether any retrieved
lead points at the actual resolution. It prints a hit-rate against the **60%
exit bar** from `design-v2.md` §8.

The judge is a first pass, not an oracle: **scan the MISS rows and a few HITs
yourself.** A low hit-rate has three very different causes worth separating —
too few cases indexed (coverage), extraction losing the useful detail (prompt),
or retrieval ranking badly (embedding/chunking).

## Notes

- Everything is re-runnable. Raw threads on disk are the source of truth; improve
  the prompt and reprocess at will. The structured cases are a derived artifact.
- Extraction runs one thread per call at temperature 0 for deterministic output.
- Failures (unparseable model output) go to `structured_cases.jsonl.failures.jsonl`
  rather than being dropped silently — inspect them before a full back-fill.
- `upload_cases` writes `cases/{channel_id}/{thread_ts}.json`, the same key the
  live Lambda uses, so re-resolving a backfilled thread in Slack later updates
  that case instead of creating a duplicate.
- Before backfilling, clear any test/experiment cases out of the `cases/` prefix
  and re-sync, so they don't pollute retrieval.
