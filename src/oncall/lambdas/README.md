# lambdas (deployed live track)

The Lambda handlers behind the Slack Events API Function URLs, kept as an
importable package so they are linted and unit-tested like the rest of the repo.

- `post_events.py`   live ingestion: message event → S3 thread doc; on a
  resolution signal, runs the shared extraction prompt and writes a redacted,
  confidence-gated structured case → Bedrock KB sync
- `questions.py`     on-demand answers: @-mention → KB retrieve_and_generate → thread reply
- `live_extract.py`  the extraction step for the live path (same prompt, gate,
  and cutoff as the batch pipeline)
- `slack_verify.py`  shared Slack HMAC signature verification + response helper

## How the live write path works now

Every message event is appended to its thread's JSON document under
`S3_PREFIX` (default `events/`) — that prefix is the raw audit trail and must
**not** be indexed. When a message carries a resolution signal, the whole
thread is distilled through `oncall.prompts.EXTRACTION_SYSTEM_PROMPT`
(resolution judgment, secret/PII redaction, confidence score); only cases with
`is_resolved` and `confidence >= CONFIDENCE_CUTOFF` (default 0.4) are written
under `S3_CASES_PREFIX` (default `cases/`), and the KB sync fires only then.

> **Action required in AWS:** point the Bedrock Knowledge Base data source at
> the `cases/` prefix (it previously indexed the raw `events/` docs, which are
> unredacted and unfiltered). Delete/re-sync any previously indexed raw docs.

Slack retry deliveries (`x-slack-retry-num` header) are dropped because the
timeline append is not idempotent.

## Environment variables

`post_events.py`: `SLACK_SIGNING_SECRET`, `S3_BUCKET_NAME`, `S3_PREFIX`,
`S3_CASES_PREFIX`, `BEDROCK_KB_ID`, `BEDROCK_DATA_SOURCE_ID`,
`BEDROCK_MODEL_ID`, `CONFIDENCE_CUTOFF`, `SLACK_WORKSPACE_URL` (e.g.
`https://yourworkspace.slack.com`, used to build case permalinks).

`questions.py`: `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`, `BEDROCK_KB_ID`,
`BEDROCK_MODEL_ARN`, `AWS_REGION_NAME`.

## Deployment (AWS Console friendly)

Each handler deploys as its own Lambda. Run:

```bash
make lambda_zips
```

then in each Lambda's console page: **Code → Upload from → .zip file** and pick
`dist/events-lambda.zip` (ingestion Lambda) or `dist/questions-lambda.zip`
(bot Lambda), then Deploy. Inside each zip the entry file is named
`lambda_function.py`, so the existing handler setting
(`lambda_function.lambda_handler`) keeps working — no handler change needed.
The handlers import their helper files with a flat-layout fallback, which is
why the multi-file zip works without any package structure.

The inline console editor still works for small tweaks afterwards — the zip
just seeds it with all the files. Keep the repo as the source of truth and
re-run `make lambda_zips` after edits rather than editing only in the console.

## Known gaps (tracked)

- Extraction runs synchronously in the event handler; at ~6 msgs/day that is
  fine, but at higher volume move it behind a queue (SQS/async invoke).
