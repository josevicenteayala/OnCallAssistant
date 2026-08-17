"""Live extraction — distill a resolved live thread into a structured case.

Runs the same extraction prompt as the batch pipeline (oncall.prompts) over a
thread document accumulated by post_events.py, so the live path gets the same
resolution judgment, secret/PII redaction, and confidence scoring as the
back-fill. Only cases passing should_index() belong in the Knowledge Base.

Required environment variables:
  BEDROCK_MODEL_ID    – Converse-capable model id / inference profile
  CONFIDENCE_CUTOFF   – min confidence to index (default 0.4, matches CLAUDE.md)
"""
import json
import logging
import os

import boto3

try:
    from oncall.extract.parsing import REQUIRED_FIELDS, parse_case
    from oncall.prompts import EXTRACTION_SYSTEM_PROMPT, build_user_message
except ImportError:  # flat Lambda zip layout (no package prefix)
    from parsing import REQUIRED_FIELDS, parse_case
    from prompts import EXTRACTION_SYSTEM_PROMPT, build_user_message

logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID  = os.environ.get("BEDROCK_MODEL_ID", "")
CONFIDENCE_CUTOFF = float(os.environ.get("CONFIDENCE_CUTOFF", "0.4"))

# Lazy client so the module imports cleanly without AWS credentials/region.
_runtime_client = None


def _runtime():
    global _runtime_client
    if _runtime_client is None:
        _runtime_client = boto3.client("bedrock-runtime")
    return _runtime_client


def thread_to_messages(thread_doc: dict) -> list[dict]:
    """Map a live thread doc's timeline to the shape the extraction prompt expects
    (same as normalize.py's output: author / ts / text)."""
    return [
        {
            "author": entry.get("user_id") or "unknown",
            "ts": entry.get("ts", ""),
            "text": entry.get("text", ""),
        }
        for entry in thread_doc.get("timeline", [])
    ]


def extract_case(thread_doc: dict, permalink: str) -> dict | None:
    """Run the extraction prompt over a live thread. Returns the case, or None
    if the model output would not parse into the required schema."""
    if not BEDROCK_MODEL_ID:
        logger.warning("BEDROCK_MODEL_ID not set — skipping live extraction")
        return None

    user_msg = build_user_message(
        permalink, json.dumps({"messages": thread_to_messages(thread_doc)})
    )
    resp = _runtime().converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": EXTRACTION_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 1500},
    )
    raw = resp["output"]["message"]["content"][0]["text"]

    case = parse_case(raw)
    if not case or not REQUIRED_FIELDS.issubset(case.keys()):
        logger.warning("Live extraction output did not parse to the required schema")
        return None

    # Trust the prompt's permalink, but pin it just in case (as extract.py does).
    case["permalink"] = permalink
    return case


def should_index(case: dict) -> bool:
    """Same gate as the batch index step: resolved and confident enough."""
    return bool(case.get("is_resolved")) and case.get("confidence", 0) >= CONFIDENCE_CUTOFF
