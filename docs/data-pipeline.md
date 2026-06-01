# On-Call Assistant — Data Pipeline (PoC steps 1–4)

This is the first slice of the PoC: turn three years of one Slack channel into a
set of structured, quality-checked incident cases. Once this produces good
output, the next slice indexes those cases and answers questions over them.

```
slack_export.py  →  normalize.py  →  extract.py  →  validate.py
   (Slack API)       (local)          (Bedrock)      (HTML report)
```

## What you do vs. what's automated

**You (one-time setup, credentials):**
- Create a Slack app and install it to the workspace; grant the bot scopes
  `channels:history`, `channels:read`, `users:read` (add `groups:*` for a
  private channel). Get the channel ID (in Slack: channel → View details).
- Enable **Amazon Bedrock model access** for the model you'll use plus Titan
  embeddings, and have AWS credentials available locally (env vars or a profile).
- Keep tokens in your environment / a secret manager — never in these files.

**Automated (these scripts):** export, clean, extract, and report.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export SLACK_BOT_TOKEN=xoxb-...            # your Slack bot token
export AWS_REGION=us-east-1                # your Bedrock region
export BEDROCK_MODEL_ID=...                # the model id you enabled in Bedrock
```

> `BEDROCK_MODEL_ID` is intentionally not hardcoded: the right value depends on
> the model and region you enable, and Bedrock often expects a region-specific
> inference-profile id. Use whatever appears in the Bedrock console for the model
> you turned on.

## Run order

```bash
# 1. Export (reads Slack only)
python slack_export.py --channel C0XXXXXXX --years 3 --outdir ./data

# 2. Normalize (local transform)
python normalize.py --indir ./data --outfile ./data/normalized_threads.jsonl

# 3. Extract — START SMALL: ~30 threads first to check quality and cost
python extract.py --infile ./data/normalized_threads.jsonl \
                  --out ./data/structured_cases.jsonl --limit 30

# 4. Validate — open the HTML and eyeball it
python validate.py --threads ./data/normalized_threads.jsonl \
                   --cases ./data/structured_cases.jsonl \
                   --out ./data/validation_report.html
```

When the 30-thread sample looks right, re-run step 3 with `--limit 0` to process
the whole corpus, then re-run step 4.

## What to look for in the report

The report sorts cases by confidence and flags each **OK** (would be indexed:
resolved and confidence ≥ 0.4) or **DROP** (held for review). Check:

- **Are the OK rows real fixes?** If a high-confidence row has a vague or wrong
  `solution`, the prompt needs tightening (in `prompts.py`) — re-run, don't patch
  data by hand.
- **Are good DROP rows being lost?** If useful resolutions sit just under 0.4, the
  cutoff (used in `extract.py`/the index step and shown in `validate.py`) is too
  high. This is how you pick the real threshold.
- **Do categories match how your team talks?** Adjust the controlled vocabulary
  and the category definition in the prompt if the mapping feels off.
- **Did redaction fire where it should?** Spot-check any row with `redaction_applied`
  true, and scan a few false ones for missed secrets.
- **Coverage:** what fraction of threads end up indexable? That number is an early
  read on whether the channel is rich enough to be worth the full build.

## Notes

- Everything is re-runnable. Raw threads on disk are the source of truth; improve
  the prompt and reprocess at will. The structured cases are a derived artifact.
- Extraction runs one thread per call at temperature 0 for deterministic output.
- Failures (unparseable model output) go to `structured_cases.jsonl.failures.jsonl`
  rather than being dropped silently — inspect them before a full back-fill.
