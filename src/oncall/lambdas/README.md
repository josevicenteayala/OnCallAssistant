# lambdas (deployed live track)

The two Lambda handlers behind the Slack Events API Function URLs, kept as an
importable package so they are linted and unit-tested like the rest of the repo.

- `post_events.py`   live ingestion: message event → S3 thread doc → Bedrock KB sync
- `questions.py`     on-demand answers: @-mention → KB retrieve_and_generate → thread reply
- `slack_verify.py`  shared Slack HMAC signature verification + response helper

## Deployment

Each handler deploys as its own Lambda. The handler entry points are
`post_events.lambda_handler` and `questions.lambda_handler`.

Package `slack_verify.py` alongside the handler file in the zip — the handlers
import it with a flat-layout fallback, so a simple

```bash
cd src/oncall/lambdas && zip function.zip post_events.py slack_verify.py
```

works with the Lambda handler set to `post_events.lambda_handler`. (The
currently deployed function was created from a single `lambda_function.py`
with handler `lambda_function.lambda_handler`; on the next deploy, update the
handler setting to the new module name.)

## Known gaps (tracked)

- `post_events.py` indexes every message raw — it bypasses the extraction /
  redaction / confidence pipeline in `oncall.prompts`. Reconciliation is the
  next slice.
- `questions.py` answers don't yet cite Slack permalinks, and it triggers a KB
  sync per message rather than debouncing.
