# lambdas (deployed live track)

The Lambda handlers behind the Slack Events API Function URLs, kept as an
importable package so they are linted and unit-tested like the rest of the
repo. **Verified end-to-end in production on 2026-08-21**: incident thread →
extraction → Knowledge Base → cited answer in Slack.

- `post_events.py`   live ingestion: message event → S3 thread doc; on a
  resolution signal, runs the shared extraction prompt and writes a redacted,
  confidence-gated structured case → Bedrock KB sync
- `questions.py`     on-demand answers: @-mention → KB retrieve_and_generate →
  threaded reply with permalink citations rebuilt deterministically in code
- `live_extract.py`  the extraction step for the live path (same prompt, gate,
  and cutoff as the batch pipeline)
- `slack_verify.py`  shared Slack HMAC signature verification + response helper

## How the live write path works

Every message event is appended to its thread's JSON document under
`S3_PREFIX` (default `events/`) — that prefix is the raw audit trail and must
**not** be indexed. When a threaded reply carries a resolution signal (one of:
*resolved, fixed, mitigation, recovery, restored, mitigated* — root messages
are always classified "alert"), the whole thread is distilled through
`oncall.prompts.EXTRACTION_SYSTEM_PROMPT` (resolution judgment, secret/PII
redaction, confidence score); only cases with `is_resolved` and
`confidence >= CONFIDENCE_CUTOFF` (default 0.4) are written under
`S3_CASES_PREFIX` (default `cases/`), and the KB sync fires only then.
Re-resolving a thread overwrites its single case (same key), so posting one
more resolution reply is also how you re-extract a thread after a fix.

**The Bedrock Knowledge Base data source must point at `cases/` only** —
never at `events/`, which is unredacted and unfiltered. This is configured
and verified in the current deployment.

Slack retry deliveries (`x-slack-retry-num` header) are dropped by both
handlers: Slack redelivers after ~3s and both the timeline append and the
answer post are non-idempotent.

## How the read path builds citations

The model's own `Sources:` output is untrusted — production showed it can leak
internal citation markers (`%[2]%`), and the retrieved chunks can be split
before the JSON's `permalink` field or arrive with empty `retrievedReferences`.
`questions.py` therefore strips whatever the model wrote and rebuilds the
Sources section from the deduped union of: (1) permalinks derived from each
citation's S3 key (`cases/{channel_id}/{thread_ts}.json` + `SLACK_WORKSPACE_URL`),
(2) permalink text found in chunk contents, (3) URLs the model transcribed.
The final posted message is logged under `--- FINAL ---`.

## Environment variables

`post_events.py`: `SLACK_SIGNING_SECRET`, `S3_BUCKET_NAME`, `S3_PREFIX`,
`S3_CASES_PREFIX`, `BEDROCK_KB_ID`, `BEDROCK_DATA_SOURCE_ID`,
`BEDROCK_MODEL_ID` (Converse-capable generation model — never an embeddings
model), `CONFIDENCE_CUTOFF`, `SLACK_WORKSPACE_URL`, `LOG_LEVEL`.

`questions.py`: `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`, `BEDROCK_KB_ID`,
`BEDROCK_MODEL_ARN`, `AWS_REGION_NAME`, `SLACK_WORKSPACE_URL`.

`SLACK_WORKSPACE_URL` is the workspace origin only, e.g.
`https://personalvin.slack.com` — no `/archives/...` path.

## Operational requirements (learned in production)

- **Timeout ≥ 60s on both Lambdas.** The default 3s kills the Bedrock calls
  silently (`Status: timeout` in the REPORT line, no error logged).
- **Events Lambda IAM**: `s3:GetObject`/`s3:PutObject` on **both**
  `events/*` and `cases/*`, `s3:ListBucket` on the bucket (so missing keys
  return NoSuchKey, not AccessDenied), `bedrock:StartIngestionJob` on the KB,
  and `bedrock:InvokeModel` on **both** the inference-profile ARN and the
  region-wildcarded foundation-model ARN (global profiles route cross-region).
- **Questions Lambda IAM**: `bedrock:RetrieveAndGenerate` + `bedrock:Retrieve`
  on the KB and `bedrock:InvokeModel` on the answer model.

## Troubleshooting (symptom → meaning)

| Log symptom | Meaning |
|---|---|
| `REPORT ... Status: timeout`, no error line | Lambda timeout too low — raise to 60s |
| `Ignoring Slack retry delivery #N` | Normal — Slack redelivered after 3s |
| `Bedrock sync failed ... ConflictException` | Normal — a sync was already running; a later case write re-syncs |
| `Case gated out of the index — is_resolved=... confidence=...` | Working as designed: thread judged too weak to index |
| Answer `Sorry, I am unable to assist...` with `citations=0` | Bedrock found nothing at retrieval; check the `Debug retrieve:` lines that follow — `0 chunks` means the KB is empty/stale (sync it), `N chunk(s) but generation used none` means a model/template issue |
| Bot reply missing in Slack | It replies **in the thread** of the mention — check the thread, then `Slack chat.postMessage returned ok=false: <error>` (WARNING level) |
| `--- FINAL ---` block | Exactly what was posted to Slack — compare against the raw `--- ANSWER ---` above it |

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
- The reply footer counts Bedrock citation objects, which can differ from the
  number of Sources links (Bedrock sometimes groups references). Cosmetic.
- Bot @-mention questions are themselves ingested as threads into `events/`;
  the confidence gate keeps them out of the index, but the Phase-1 trigger
  classifier is the proper filter.
