# current-lambdas — deployed-code snapshot

Copies of the code running in the AWS Lambda console as of 2026-08-17,
kept for reference/diffing only. The source of truth is `src/oncall/lambdas/`;
its logging and robustness improvements from these snapshots have been ported.
Do not edit here — edit the package and redeploy with `make lambda_zips`.
