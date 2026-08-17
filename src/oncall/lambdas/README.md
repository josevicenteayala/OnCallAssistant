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

## Deployment

Each handler deploys as its own Lambda. Entry points:
`post_events.lambda_handler` and `questions.lambda_handler`.

The handlers import their helpers with a flat-layout fallback, so zip the
handler with its dependencies flat:

```bash
cd src/oncall/lambdas && zip function.zip post_events.py live_extract.py slack_verify.py
cd ../../..; zip -j src/oncall/lambdas/function.zip src/oncall/prompts.py src/oncall/extract/parsing.py
```

with the Lambda handler set to `post_events.lambda_handler`. The questions
Lambda needs `questions.py`, `slack_verify.py`, and `prompts.py`. (The currently
deployed function was created from a single `lambda_function.py` with handler
`lambda_function.lambda_handler`; on the next deploy, update the handler
setting to the new module name.)

## Known gaps (tracked)

- Extraction runs synchronously in the event handler; at ~6 msgs/day that is
  fine, but at higher volume move it behind a queue (SQS/async invoke).
