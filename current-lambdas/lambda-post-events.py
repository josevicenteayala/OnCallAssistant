import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Module-level setup — executed once per warm container
# ---------------------------------------------------------------------------

# In AWS Lambda the runtime already attaches a handler to the root logger, so
# calling ``logging.basicConfig`` here is a no-op on warm invocations and can
# even suppress log records on cold starts. Configure our module logger
# explicitly instead and honour a ``LOG_LEVEL`` env var for on-the-fly tuning.
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SLACK_SIGNING_SECRET    = os.environ.get("SLACK_SIGNING_SECRET", "")
S3_BUCKET_NAME          = os.environ.get("S3_BUCKET_NAME", "")
S3_PREFIX               = os.environ.get("S3_PREFIX", "events/")
BEDROCK_KB_ID           = os.environ.get("BEDROCK_KB_ID", "")
BEDROCK_DATA_SOURCE_ID  = os.environ.get("BEDROCK_DATA_SOURCE_ID", "")

s3_client      = boto3.client("s3")
bedrock_client = boto3.client("bedrock-agent")

logger.info(
    "Lambda container init — s3_bucket=%s s3_prefix=%s bedrock_kb_id=%s "
    "bedrock_ds_id=%s signing_secret_configured=%s",
    S3_BUCKET_NAME or "<unset>",
    S3_PREFIX,
    BEDROCK_KB_ID or "<unset>",
    BEDROCK_DATA_SOURCE_ID or "<unset>",
    bool(SLACK_SIGNING_SECRET),
)

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
        logger.debug("Thread subject did not match incident regex — text=%r", text)
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

    logger.debug(
        "Parsed incident header — org=%s namespace=%s service=%s operation=%s target=%s",
        org.strip(), namespace, service.strip(), operation, target_svc,
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
        logger.debug("Fetching existing thread from S3 — bucket=%s key=%s",
                     S3_BUCKET_NAME, s3_key)
        obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        payload = json.loads(obj["Body"].read())
        logger.info(
            "Fetched existing thread from S3 — key=%s messages=%d",
            s3_key, len(payload.get("timeline", [])),
        )
        return payload
    except s3_client.exceptions.NoSuchKey:
        logger.info("No existing thread in S3 — key=%s (will create)", s3_key)
        return None
    except ClientError as exc:
        logger.warning(
            "Could not fetch existing thread from S3 — key=%s error=%s",
            s3_key, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Bedrock sync
# ---------------------------------------------------------------------------

def _trigger_bedrock_sync() -> None:
    """Start a Bedrock Knowledge Base ingestion job; log but never raise."""
    if not BEDROCK_KB_ID or not BEDROCK_DATA_SOURCE_ID:
        logger.warning(
            "Skipping Bedrock sync — kb_id=%s data_source_id=%s",
            BEDROCK_KB_ID or "<unset>", BEDROCK_DATA_SOURCE_ID or "<unset>",
        )
        return

    started = time.time()
    try:
        logger.info(
            "Starting Bedrock ingestion job — kb_id=%s data_source_id=%s",
            BEDROCK_KB_ID, BEDROCK_DATA_SOURCE_ID,
        )
        response = bedrock_client.start_ingestion_job(
            knowledgeBaseId=BEDROCK_KB_ID,
            dataSourceId=BEDROCK_DATA_SOURCE_ID,
        )
        job_id = response.get("ingestionJob", {}).get("ingestionJobId", "unknown")
        logger.info(
            "Bedrock sync started — ingestionJobId=%s duration_ms=%d",
            job_id, int((time.time() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Bedrock sync failed (non-fatal) — duration_ms=%d error=%s",
            int((time.time() - started) * 1000), exc,
        )


# ---------------------------------------------------------------------------
# Slack signature verification
# ---------------------------------------------------------------------------

def _verify_slack_signature(headers: dict, raw_body: str) -> bool:
    timestamp       = headers.get("x-slack-request-timestamp", "")
    slack_signature = headers.get("x-slack-signature", "")

    if not timestamp or not slack_signature:
        logger.warning(
            "Missing Slack signature headers — has_timestamp=%s has_signature=%s",
            bool(timestamp), bool(slack_signature),
        )
        return False

    if not SLACK_SIGNING_SECRET:
        logger.error("SLACK_SIGNING_SECRET is not configured — refusing to verify")
        return False

    # Reject requests older than 5 minutes to prevent replay attacks
    try:
        skew = abs(time.time() - int(timestamp))
    except ValueError:
        logger.warning("Slack timestamp is not an integer — value=%r", timestamp)
        return False

    if skew > 300:
        logger.warning(
            "Request timestamp too old — skew_seconds=%d (possible replay attack)",
            int(skew),
        )
        return False

    base_string = f"v0:{timestamp}:{raw_body}"
    computed    = hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(f"v0={computed}", slack_signature):
        logger.warning("Slack signature mismatch — skew_seconds=%d", int(skew))
        return False

    logger.debug("Slack signature valid — skew_seconds=%d", int(skew))
    return True


# ---------------------------------------------------------------------------
# Response helper
# ---------------------------------------------------------------------------

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    invocation_started = time.time()
    request_id         = getattr(context, "aws_request_id", "local")
    remaining_ms       = (
        context.get_remaining_time_in_millis()
        if context and hasattr(context, "get_remaining_time_in_millis")
        else -1
    )
    logger.info(
        "Lambda invocation start — request_id=%s remaining_ms=%d "
        "http_method=%s path=%s source_ip=%s",
        request_id,
        remaining_ms,
        event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod", "n/a"),
        event.get("rawPath")
            or event.get("path", "n/a"),
        event.get("requestContext", {}).get("http", {}).get("sourceIp", "n/a"),
    )

    try:
        # ------------------------------------------------------------------
        # STEP 1 — Parse incoming request
        # ------------------------------------------------------------------
        logger.info("[step=1] Parsing incoming request — request_id=%s", request_id)
        headers  = {k.lower(): v for k, v in event.get("headers", {}).items()}
        raw_body = event.get("body", "")
        is_b64   = event.get("isBase64Encoded", False)
        if is_b64:
            logger.debug("[step=1] Body is base64-encoded — decoding")
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        logger.debug(
            "[step=1] Headers received — count=%d has_slack_signature=%s "
            "has_slack_timestamp=%s body_bytes=%d base64=%s",
            len(headers),
            "x-slack-signature" in headers,
            "x-slack-request-timestamp" in headers,
            len(raw_body or ""),
            is_b64,
        )

        body       = json.loads(raw_body)
        event_type = body.get("type")
        logger.info(
            "[step=1] Parsed Slack payload — request_id=%s event_type=%s",
            request_id, event_type,
        )

        # ------------------------------------------------------------------
        # STEP 2 — Handle URL verification challenge (no auth needed)
        # ------------------------------------------------------------------
        if event_type == "url_verification":
            logger.info(
                "[step=2] Responding to Slack URL verification challenge — request_id=%s",
                request_id,
            )
            return _response(200, {"challenge": body.get("challenge")})

        # ------------------------------------------------------------------
        # STEP 3 — Verify Slack signature
        # ------------------------------------------------------------------
        logger.info("[step=3] Verifying Slack signature — request_id=%s", request_id)
        if not _verify_slack_signature(headers, raw_body):
            logger.warning(
                "[step=3] Rejecting request — invalid Slack signature "
                "request_id=%s", request_id,
            )
            return _response(403, {"error": "Invalid Slack signature"})

        logger.info("[step=3] Slack signature verified — request_id=%s", request_id)

        # ------------------------------------------------------------------
        # STEP 4 — Filter events (only plain user messages)
        # ------------------------------------------------------------------
        slack_event = body.get("event", {})
        slack_event_type = slack_event.get("type")
        slack_subtype    = slack_event.get("subtype")
        logger.info(
            "[step=4] Filtering event — request_id=%s outer_type=%s "
            "inner_type=%s subtype=%s bot_id=%s",
            request_id, event_type, slack_event_type, slack_subtype,
            slack_event.get("bot_id"),
        )
        if (
            event_type != "event_callback"
            or slack_event_type != "message"
            or slack_subtype is not None
        ):
            logger.info(
                "[step=4] Ignoring non-message or bot/edit event — "
                "request_id=%s outer_type=%s inner_type=%s subtype=%s",
                request_id, event_type, slack_event_type, slack_subtype,
            )
            return _response(200, {"message": "Event ignored"})

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
        logger.info(
            "[step=5] Resolved thread identity — request_id=%s channel_id=%s "
            "ts=%s thread_ts=%s is_root=%s text_len=%d s3_key=%s",
            request_id, channel_id, ts, thread_ts, is_root, len(text or ""), s3_key,
        )

        # ------------------------------------------------------------------
        # STEP 6 — Load existing thread doc or create a new one
        # ------------------------------------------------------------------
        logger.info(
            "[step=6] Loading thread document from S3 — request_id=%s key=%s",
            request_id, s3_key,
        )
        existing = _get_existing_thread(s3_key)

        if existing:
            thread_doc = existing
            incident   = thread_doc["incident"]
            logger.info(
                "[step=6] Appending to existing thread — request_id=%s "
                "key=%s existing_messages=%d",
                request_id, s3_key, len(thread_doc.get("timeline", [])),
            )
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
                    "ingested_at": datetime.utcnow().isoformat(),
                },
                "incident":  incident,
                "timeline":  [],
                "document":  "",
            }
            logger.info(
                "[step=6] Creating new thread document — request_id=%s "
                "key=%s parsed_header=%s",
                request_id, s3_key, "issue_summary" not in (incident or {}) or "document" in (incident or {}),
            )

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
        thread_doc["_meta"]["last_updated"]     = datetime.utcnow().isoformat()
        thread_doc["_meta"]["message_count"]    = len(thread_doc["timeline"])
        logger.info(
            "[step=7] Appended message to timeline — request_id=%s "
            "role=%s user_id=%s messages=%d",
            request_id, role, slack_event.get("user"),
            len(thread_doc["timeline"]),
        )

        # ------------------------------------------------------------------
        # STEP 8 — Write back to S3 (same key → Bedrock KB deduplicates on sync)
        # ------------------------------------------------------------------
        put_started = time.time()
        logger.info(
            "[step=8] Writing thread document to S3 — request_id=%s "
            "bucket=%s key=%s",
            request_id, S3_BUCKET_NAME, s3_key,
        )
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=json.dumps(thread_doc, indent=2),
            ContentType="application/json",
        )
        logger.info(
            "[step=8] Thread stored in S3 — request_id=%s key=%s role=%s "
            "messages=%d duration_ms=%d",
            request_id, s3_key, role, len(thread_doc["timeline"]),
            int((time.time() - put_started) * 1000),
        )

        # ------------------------------------------------------------------
        # STEP 9 — Trigger Bedrock Knowledge Base sync (best-effort)
        # ------------------------------------------------------------------
        logger.info(
            "[step=9] Triggering Bedrock ingestion sync — request_id=%s", request_id,
        )
        _trigger_bedrock_sync()

        # ------------------------------------------------------------------
        # STEP 10 — Return success
        # ------------------------------------------------------------------
        logger.info(
            "[step=10] Lambda invocation success — request_id=%s "
            "total_duration_ms=%d",
            request_id, int((time.time() - invocation_started) * 1000),
        )
        return _response(200, {"message": "Event stored successfully"})

    except json.JSONDecodeError as exc:
        logger.exception(
            "Failed to parse request body as JSON — request_id=%s error=%s",
            request_id, exc,
        )
        return _response(400, {"error": "Invalid JSON body"})
    except ClientError as exc:
        logger.exception(
            "AWS client error while handling event — request_id=%s error=%s",
            request_id, exc,
        )
        return _response(500, {"error": "Failed to store event"})
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected error in Lambda handler — request_id=%s error=%s",
            request_id, exc,
        )
        return _response(500, {"error": "Internal server error"})
    finally:
        logger.info(
            "Lambda invocation end — request_id=%s total_duration_ms=%d",
            request_id, int((time.time() - invocation_started) * 1000),
        )