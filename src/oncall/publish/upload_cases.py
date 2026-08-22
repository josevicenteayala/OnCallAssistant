"""Backfill step - publish extracted cases to the Knowledge Base's S3 prefix.

Splits structured_cases.jsonl into one JSON object per indexable case
(is_resolved and confidence >= cutoff — the same gate as the index step and
the live path), keyed {prefix}{channel_id}/{thread_ts}.json to match the
live path's layout. That way the KB treats backfilled and live cases
identically, and a later live re-resolution of the same thread overwrites
its backfilled case instead of duplicating it.

The channel id is read from each case's Slack permalink; --channel is the
fallback for cases whose permalink doesn't parse.

Requires AWS credentials in the environment. With --sync, also starts a KB
ingestion job (needs BEDROCK_KB_ID and BEDROCK_DATA_SOURCE_ID env vars).

Usage:
    python -m oncall.publish.upload_cases --cases ./data/structured_cases.jsonl \
        --bucket bucket-on-call-post --sync
"""
import argparse
import json
import os
import re
import sys

try:
    import boto3
except ImportError:
    sys.exit("Missing dependency. Run: make install")

_CHANNEL_RE = re.compile(r"/archives/(C[A-Z0-9]+)/")


def channel_from_permalink(permalink: str | None) -> str | None:
    m = _CHANNEL_RE.search(permalink or "")
    return m.group(1) if m else None


def indexable(case: dict, cutoff: float) -> bool:
    """Same gate as the index step and live_extract.should_index."""
    return bool(case.get("is_resolved")) and case.get("confidence", 0) >= cutoff


def case_key(prefix: str, channel_id: str, thread_ts: str) -> str:
    return f"{prefix}{channel_id}/{thread_ts}.json"


def select_cases(cases: list[dict], cutoff: float, channel_fallback: str | None):
    """Yield (key-parts, case) for uploadable cases; report what was skipped.

    Returns (uploadable, skipped_gate, skipped_no_channel) where uploadable is
    a list of (channel_id, thread_ts, case).
    """
    uploadable, skipped_gate, skipped_no_channel = [], 0, 0
    for case in cases:
        if not indexable(case, cutoff):
            skipped_gate += 1
            continue
        channel = channel_from_permalink(case.get("permalink")) or channel_fallback
        thread_ts = case.get("thread_ts")
        if not channel or not thread_ts:
            skipped_no_channel += 1
            continue
        uploadable.append((channel, thread_ts, case))
    return uploadable, skipped_gate, skipped_no_channel


def run(cases_path, bucket, prefix, cutoff, channel_fallback, dry_run, sync):
    with open(cases_path) as f:
        cases = [json.loads(line) for line in f]

    uploadable, skipped_gate, skipped_no_channel = select_cases(
        cases, cutoff, channel_fallback
    )
    print(f"{len(cases)} cases read: {len(uploadable)} indexable, "
          f"{skipped_gate} gated out (unresolved / confidence < {cutoff}), "
          f"{skipped_no_channel} missing channel or thread_ts")
    if skipped_no_channel and not channel_fallback:
        print("  (pass --channel C0XXXXXXX to give those a fallback channel id)")

    if dry_run:
        for channel, thread_ts, _ in uploadable:
            print(f"  would upload s3://{bucket}/{case_key(prefix, channel, thread_ts)}")
        return

    s3 = boto3.client("s3")
    for channel, thread_ts, case in uploadable:
        key = case_key(prefix, channel, thread_ts)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(case, ensure_ascii=False),
            ContentType="application/json",
        )
        print(f"  uploaded s3://{bucket}/{key}")
    print(f"Done. {len(uploadable)} cases -> s3://{bucket}/{prefix}")

    if sync:
        kb_id = os.environ.get("BEDROCK_KB_ID")
        ds_id = os.environ.get("BEDROCK_DATA_SOURCE_ID")
        if not kb_id or not ds_id:
            sys.exit("--sync needs BEDROCK_KB_ID and BEDROCK_DATA_SOURCE_ID set.")
        resp = boto3.client("bedrock-agent").start_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id
        )
        job_id = resp.get("ingestionJob", {}).get("ingestionJobId", "unknown")
        print(f"KB ingestion job started: {job_id}")
    else:
        print("Now sync the Knowledge Base data source (console, or re-run with --sync).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Upload indexable cases to the KB's S3 prefix.")
    p.add_argument("--cases", default="./data/structured_cases.jsonl")
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET_NAME", ""),
                   help="Target bucket (default: S3_BUCKET_NAME env var).")
    p.add_argument("--prefix", default="cases/")
    p.add_argument("--cutoff", type=float, default=0.4,
                   help="Min confidence to upload (tune from the validation report).")
    p.add_argument("--channel", default=None,
                   help="Fallback channel id for cases whose permalink doesn't parse.")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be uploaded without touching S3.")
    p.add_argument("--sync", action="store_true",
                   help="Start a KB ingestion job after uploading.")
    args = p.parse_args()
    if not args.bucket:
        sys.exit("Set --bucket or the S3_BUCKET_NAME env var.")
    run(args.cases, args.bucket, args.prefix, args.cutoff,
        args.channel, args.dry_run, args.sync)
