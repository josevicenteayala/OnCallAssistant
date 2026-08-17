"""Live ingestion Lambda — Slack Events API → S3 thread docs → Bedrock KB sync.

Deployed behind a Lambda Function URL. Each Slack message event is appended to
its thread's JSON document in S3 (keyed on thread_ts so replies land in the
parent's file) and a Bedrock Knowledge Base ingestion job is triggered
best-effort.

Handler: post_events.lambda_handler (see this package's README for packaging).

Required environment variables:
  SLACK_SIGNING_SECRET    – used to verify Slack HMAC signatures
  S3_BUCKET_NAME          – bucket for thread documents
  S3_PREFIX               – key prefix (default "events/")
  BEDROCK_KB_ID           – Bedrock Knowledge Base ID
  BEDROCK_DATA_SOURCE_ID  – KB data source to sync after each write
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from oncall.lambdas.slack_verify import response, verify_slack_signature
except ImportError:  # flat Lambda zip layout (no package prefix)
    from slack_verify import response, verify_slack_signature

# ---------------------------------------------------------------------------
# Module-level setup — executed once per warm container
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SLACK_SIGNING_SECRET    = os.environ.get("SLACK_SIGNING_SECRET", "")
S3_BUCKET_NAME          = os.environ.get("S3_BUCKET_NAME", "")
S3_PREFIX               = os.environ.get("S3_PREFIX", "events/")
BEDROCK_KB_ID           = os.environ.get("BEDROCK_KB_ID", "")
BEDROCK_DATA_SOURCE_ID  = os.environ.get("BEDROCK_DATA_SOURCE_ID", "")

# boto3 clients are created lazily so the module imports cleanly (tests, tools)
# without AWS credentials or a region configured.
_s3_client      = None
_bedrock_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-agent")
    return _bedrock_client


# ---------------------------------------------------------------------------
# Slack thread format parser
# Pattern: {org} ({region}/{env}/{cluster}) | ({tag}) {service} ({protocol}) - {issue}
# ---------------------------------------------------------------------------

_THREAD_RE = re.compile(
    r"^(.+?)\s+\(([^/]+)/([^/]+)/([^)]+)\)"        # org (region/env/cluster)
    r"\s+\|\s+\(([^)]+)\)\s+(.+?)\s+\(([^)]+)\)"   # | (tag) service (protocol)
    r"\s+-\s+(.+)$"                                  # - issue
)
_OPERATION_RE = re.compile(r"operation\s+(.+?)\s+to\s+(\S+)$", re.IGNORECASE)


def _parse_incident_thread(text: str) -> dict | None:
    """Extract structured incident fields from a Slack thread subject line."""
    m = _THREAD_RE.match(text.strip())
    if not m:
        return None

    org, region, env, cluster, tag, service, protocol, issue = m.groups()

    op_match  = _OPERATION_RE.search(issue)
    operation   = op_match.group(1) if op_match else None
    target_svc  = op_match.group(2) if op_match else None
    namespace   = f"{region}/{env}/{cluster}"

    document = (
        f"Production incident in {org.strip()}. "
        f"Service {service.strip()} ({protocol.strip()}) [tag: {tag.strip()}] "
        f"running on cluster {cluster.strip()} ({namespace}) is reporting: {issue.strip()}."
    )

    return {
        "organization":     org.strip(),
        "region":           region.strip(),
        "environment":      env.strip(),
        "cluster":          cluster.strip(),
        "namespace_raw":    namespace,
        "service_name":     service.strip(),
        "service_tag":      tag.strip(),
        "service_protocol": protocol.strip(),
        "issue_summary":    issue.strip(),
        "operation":        operation,
        "target_service":   target_svc,
        "document":         document,
    }


# ---------------------------------------------------------------------------
# Timeline helpers
# ---------------------------------------------------------------------------

_RESOLUTION_WORDS    = {"resolved", "fixed", "mitigation", "recovery", "restored", "mitigated"}
_ACTION_WORDS        = {"rollback", "revert", "restart", "scaled", "deployed", "rerouted"}
_INVESTIGATION_WORDS = {"investigating", "looking", "checking", "confirmed", "identified", "found"}


def _classify_role(text: str, is_root: bool) -> str:
    if is_root:
        return "alert"
    lower = text.lower()
    if any(w in lower for w in _RESOLUTION_WORDS):
        return "resolution"
    if any(w in lower for w in _ACTION_WORDS):
        return "action"
    if any(w in lower for w in _INVESTIGATION_WORDS):
        return "investigation"
    return "update"


def _build_document(incident: dict, timeline: list) -> str:
    """Regenerate the embedding text from incident metadata + full timeline."""
    base = incident.get("document", "Incident report")
    steps = " → ".join(
        f"[{e.get('role', 'update')}] {e['text']}" for e in timeline
    )
    return f"{base.rstrip('.')}. Timeline: {steps}."


# ---------------------------------------------------------------------------
# S3 thread document helpers
# ---------------------------------------------------------------------------

def _get_existing_thread(s3_key: str) -> dict | None:
    """Return the existing thread document from S3, or None if absent."""
    try:
        obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        return json.loads(obj["Body"].read())
    except _s3().exceptions.NoSuchKey:
        return None
    except ClientError as exc:
        logger.warning("Could not fetch existing thread from S3: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Bedrock sync
# ---------------------------------------------------------------------------

def _trigger_bedrock_sync() -> None:
    """Start a Bedrock Knowledge Base ingestion job; log but never raise."""
    try:
        resp = _bedrock().start_ingestion_job(
            knowledgeBaseId=BEDROCK_KB_ID,
            dataSourceId=BEDROCK_DATA_SOURCE_ID,
        )
        job_id = resp.get("ingestionJob", {}).get("ingestionJobId", "unknown")
        logger.info("Bedrock sync started — ingestionJobId=%s", job_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bedrock sync failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    try:
        # ------------------------------------------------------------------
        # STEP 1 — Parse incoming request
        # ------------------------------------------------------------------
        headers  = {k.lower(): v for k, v in event.get("headers", {}).items()}
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            import base64
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        body       = json.loads(raw_body)
        event_type = body.get("type")
        logger.info("Received Slack event type=%s", event_type)

        # ------------------------------------------------------------------
        # STEP 2 — Handle URL verification challenge (no auth needed)
        # ------------------------------------------------------------------
        if event_type == "url_verification":
            logger.info("Responding to Slack URL verification challenge")
            return response(200, {"challenge": body.get("challenge")})

        # ------------------------------------------------------------------
        # STEP 3 — Verify Slack signature
        # ------------------------------------------------------------------
        if not verify_slack_signature(headers, raw_body, SLACK_SIGNING_SECRET):
            return response(403, {"error": "Invalid Slack signature"})

        logger.info("Slack signature verified")

        # ------------------------------------------------------------------
        # STEP 4 — Filter events (only plain user messages)
        # ------------------------------------------------------------------
        slack_event = body.get("event", {})
        if (
            event_type != "event_callback"
            or slack_event.get("type") != "message"
            or slack_event.get("subtype") is not None
        ):
            logger.info("Ignoring non-message or bot/edit event — skipping")
            return response(200, {"message": "Event ignored"})

        # ------------------------------------------------------------------
        # STEP 5 — Resolve thread identity
        # Always key on thread_ts so every reply lands in the parent's file.
        # A root message has no thread_ts — use ts as the thread anchor.
        # ------------------------------------------------------------------
        channel_id = slack_event.get("channel", "unknown")
        ts         = slack_event.get("ts", "unknown")
        text       = slack_event.get("text", "")
        is_root    = slack_event.get("thread_ts") is None
        thread_ts  = slack_event.get("thread_ts") if not is_root else ts
        s3_key     = f"{S3_PREFIX}{channel_id}/{thread_ts}.json"

        # ------------------------------------------------------------------
        # STEP 6 — Load existing thread doc or create a new one
        # ------------------------------------------------------------------
        existing = _get_existing_thread(s3_key)

        if existing:
            thread_doc = existing
            incident   = thread_doc["incident"]
            logger.info("Appending to existing thread — key=%s", s3_key)
        else:
            # First message in this thread — parse the incident header
            incident = _parse_incident_thread(text) or {
                "issue_summary": text,
                "document":      text,
            }
            thread_doc = {
                "_meta": {
                    "event_id":   f"{channel_id}-{thread_ts}",
                    "channel_id": channel_id,
                    "thread_ts":  thread_ts,
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
                "incident":  incident,
                "timeline":  [],
                "document":  "",
            }
            logger.info("Creating new thread document — key=%s", s3_key)

        # ------------------------------------------------------------------
        # STEP 7 — Append this message to the timeline
        # ------------------------------------------------------------------
        role = _classify_role(text, is_root)
        thread_doc["timeline"].append({
            "ts":      ts,
            "user_id": slack_event.get("user"),
            "text":    text,
            "role":    role,
        })

        # Regenerate document so the re-ingested embedding covers the full timeline
        thread_doc["document"]                  = _build_document(incident, thread_doc["timeline"])
        thread_doc["_meta"]["last_updated"]     = datetime.now(timezone.utc).isoformat()
        thread_doc["_meta"]["message_count"]    = len(thread_doc["timeline"])

        # ------------------------------------------------------------------
        # STEP 8 — Write back to S3 (same key → Bedrock KB deduplicates on sync)
        # ------------------------------------------------------------------
        _s3().put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=json.dumps(thread_doc, indent=2),
            ContentType="application/json",
        )
        logger.info("Thread stored in S3 — key=%s role=%s messages=%d",
                    s3_key, role, len(thread_doc["timeline"]))

        # ------------------------------------------------------------------
        # STEP 9 — Trigger Bedrock Knowledge Base sync (best-effort)
        # ------------------------------------------------------------------
        _trigger_bedrock_sync()

        # ------------------------------------------------------------------
        # STEP 10 — Return success
        # ------------------------------------------------------------------
        return response(200, {"message": "Event stored successfully"})

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse request body: %s", exc)
        return response(400, {"error": "Invalid JSON body"})
    except ClientError as exc:
        logger.error("AWS client error: %s", exc)
        return response(500, {"error": "Failed to store event"})
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error: %s", exc)
        return response(500, {"error": "Internal server error"})
