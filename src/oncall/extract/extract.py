#!/usr/bin/env python3
"""Step 3 - Extract structured cases via Amazon Bedrock.

Calls Bedrock (Converse API) once per normalized thread with the extraction
prompt, parses the JSON defensively, and writes:

    <out>                 structured_cases.jsonl  (successful extractions)
    <out>.failures.jsonl  threads whose output would not parse

Requires AWS credentials in the environment (or an attached role) and Bedrock
model access enabled for the chosen model. Configure via env vars:

    BEDROCK_MODEL_ID   e.g. an inference-profile / model id available in your
                       region (set this to whatever you enabled in Bedrock)
    AWS_REGION         e.g. us-east-1

Usage:
    python extract.py --infile ./data/normalized_threads.jsonl \
                      --out ./data/structured_cases.jsonl --limit 30
"""
import argparse
import json
import os
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

from oncall.prompts import EXTRACTION_SYSTEM_PROMPT, build_user_message

REQUIRED_FIELDS = {
    "is_resolved", "summary", "issue", "affected_service", "category", "tags",
    "root_cause", "troubleshooting_steps", "solution", "solution_type",
    "confidence", "permalink", "redaction_applied",
}


def strip_fences(s: str) -> str:
    """Remove ```json ... ``` fences if the model added them."""
    s = s.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def parse_case(text: str):
    """Best-effort parse of the model output into a dict, or None."""
    candidate = strip_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Fall back to the outermost braces.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def converse(client, model_id, system, user_msg, max_retries=4):
    """Call Bedrock Converse with backoff on throttling."""
    for attempt in range(max_retries):
        try:
            resp = client.converse(
                modelId=model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user_msg}]}],
                inferenceConfig={"temperature": 0, "maxTokens": 1500},
            )
            return resp["output"]["message"]["content"][0]["text"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ThrottlingException", "TooManyRequestsException") and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  throttled, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def run(infile, out, limit):
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        sys.exit("Set BEDROCK_MODEL_ID to the model you enabled in Bedrock.")
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)

    fail_path = out + ".failures.jsonl"
    ok = fail = 0
    with open(infile) as src, open(out, "w") as dst, open(fail_path, "w") as ferr:
        for i, line in enumerate(src):
            if limit and i >= limit:
                break
            thread = json.loads(line)
            user_msg = build_user_message(
                thread["permalink"], json.dumps({"messages": thread["messages"]}))
            raw = converse(client, model_id, EXTRACTION_SYSTEM_PROMPT, user_msg)
            case = parse_case(raw)
            if case and REQUIRED_FIELDS.issubset(case.keys()):
                # Trust the prompt's permalink, but pin it just in case.
                case["permalink"] = thread["permalink"]
                case["thread_ts"] = thread["thread_ts"]
                dst.write(json.dumps(case) + "\n")
                ok += 1
            else:
                ferr.write(json.dumps({
                    "thread_ts": thread["thread_ts"],
                    "permalink": thread["permalink"],
                    "raw_output": raw,
                }) + "\n")
                fail += 1
            if (ok + fail) % 25 == 0:
                print(f"  processed {ok + fail} ({fail} failures)...")

    print(f"Done. {ok} cases -> {out}")
    if fail:
        print(f"{fail} failures -> {fail_path} (inspect these before back-filling)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Extract structured cases via Bedrock.")
    p.add_argument("--infile", default="./data/normalized_threads.jsonl")
    p.add_argument("--out", default="./data/structured_cases.jsonl")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N threads (0 = all). Use ~30 first.")
    args = p.parse_args()
    run(args.infile, args.out, args.limit)
