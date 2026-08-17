"""Live ingestion Lambda — Slack Events API → S3 thread docs → extraction → KB.

Deployed behind a Lambda Function URL. Each Slack message event is appended to
its thread's JSON document in S3 (keyed on thread_ts so replies land in the
parent's file). On a resolution-signal message the whole thread runs through
the shared extraction prompt and only a redacted, confidence-gated structured
case is written to the cases/ prefix, followed by a best-effort Bedrock
Knowledge Base sync.

Handler: lambda_function.lambda_handler via `make lambda_zips` (the zip renames
this file); see this package's README for deployment details.

Required environment variables:
  SLACK_SIGNING_SECRET    – used to verify Slack HMAC signatures
  S3_BUCKET_NAME          – bucket for thread documents
  S3_PREFIX               – key prefix for raw thread docs (default "events/")
  S3_CASES_PREFIX         – key prefix for indexable cases (default "cases/")
  BEDROCK_KB_ID           – Bedrock Knowledge Base ID
  BEDROCK_DATA_SOURCE_ID  – KB data source to sync after each case write
  BEDROCK_MODEL_ID        – Converse-capable model for live extraction
  CONFIDENCE_CUTOFF       – min confidence to index (default 0.4)
  SLACK_WORKSPACE_URL     – e.g. https://yourworkspace.slack.com (permalinks)
  LOG_LEVEL               – logging level (default INFO)
"""
import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from oncall.lambdas import live_extract
    from oncall.lambdas.slack_verify import response, verify_slack_signature
except ImportError:  # flat Lambda zip layout (no package prefix)
    import live_extract
    from slack_verify import response, verify_slack_signature

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
S3_CASES_PREFIX         = os.environ.get("S3_CASES_PREFIX", "cases/")
BEDROCK_KB_ID           = os.environ.get("BEDROCK_KB_ID", "")
BEDROCK_DATA_SOURCE_ID  = os.environ.get("BEDROCK_DATA_SOURCE_ID", "")
# e.g. https://yourworkspace.slack.com — used to build case permalinks
SLACK_WORKSPACE_URL     = os.environ.get("SLACK_WORKSPACE_URL", "")

logger.info(
    "Lambda container init — s3_bucket=%s s3_prefix=%s cases_prefix=%s "
    "bedrock_kb_id=%s bedrock_ds_id=%s signing_secret_configured=%s",
    S3_BUCKET_NAME or "<unset>",
    S3_PREFIX,
    S3_CASES_PREFIX,
    BEDROCK_KB_ID or "<unset>",
    BEDROCK_DATA_SOURCE_ID or "<unset>",
    bool(SLACK_SIGNING_SECRET),
)

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
        logger.debug("Thread subject did not match incident regex — text=%r", text)
        return None

    org, region, env, cluster, tag, service, protocol, issue = m.groups()

    op_match  = _OPERATION_RE.search(issue)
    operation   = op_match.group(1) if op_match else None
    target_svc  = op_match.group(2) if op_match else None
    namespace   = f"{region}/{env}/{cluster}"

    logger.debug(
        "Parsed incident header — org=%s namespace=%s service=%s operation=%s target=%s",
        org.strip(), namespace, service.strip(), operation, target_svc,
    )

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
        logger.debug("Fetching existing thread from S3 — bucket=%s key=%s",
                     S3_BUCKET_NAME, s3_key)
        obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        payload = json.loads(obj["Body"].read())
        logger.info(
            "Fetched existing thread from S3 — key=%s messages=%d",
            s3_key, len(payload.get("timeline", [])),
        )
        return payload
    except _s3().exceptions.NoSuchKey:
        logger.info("No existing thread in S3 — key=%s (will create)", s3_key)
        return None
    except ClientError as exc:
        logger.warning(
            "Could not fetch existing thread from S3 — key=%s error=%s",
            s3_key, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Live extraction — the quality gate between raw events and the Knowledge Base
# ---------------------------------------------------------------------------

def _permalink(channel_id: str, thread_ts: str) -> str:
    """Best-effort Slack permalink for a thread (archive URL if the workspace
    domain is configured, else an app-link placeholder)."""
    if SLACK_WORKSPACE_URL:
        return (
            f"{SLACK_WORKSPACE_URL.rstrip('/')}/archives/{channel_id}"
            f"/p{thread_ts.replace('.', '')}"
        )
    return f"slack://channel/{channel_id}/{thread_ts}"


def _extract_and_index(channel_id: str, thread_ts: str, thread_doc: dict) -> None:
    """Distill a resolved thread into a structured case and index it.

    Runs the shared extraction prompt (redaction + confidence scoring), writes
    the case to the cases/ prefix — the prefix the Knowledge Base data source
    should point at — and triggers a KB sync. Best-effort: raw ingestion must
    never fail because extraction did.
    """
    started = time.time()
    try:
        case = live_extract.extract_case(thread_doc, _permalink(channel_id, thread_ts))
        if case is None:
            return
        if not live_extract.should_index(case):
            logger.info(
                "Case gated out of the index — is_resolved=%s confidence=%s "
                "duration_ms=%d",
                case.get("is_resolved"), case.get("confidence"),
                int((time.time() - started) * 1000),
            )
            return
        case["thread_ts"] = thread_ts
        case_key = f"{S3_CASES_PREFIX}{channel_id}/{thread_ts}.json"
        _s3().put_object(
            Bucket=S3_BUCKET_NAME,
            Key=case_key,
            Body=json.dumps(case, ensure_ascii=False),
            ContentType="application/json",
        )
        logger.info(
            "Structured case stored — key=%s confidence=%s duration_ms=%d",
            case_key, case.get("confidence"), int((time.time() - started) * 1000),
        )
        _trigger_bedrock_sync()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Live extraction failed (non-fatal) — duration_ms=%d error=%s",
            int((time.time() - started) * 1000), exc,
        )


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
        resp = _bedrock().start_ingestion_job(
            knowledgeBaseId=BEDROCK_KB_ID,
            dataSourceId=BEDROCK_DATA_SOURCE_ID,
        )
        job_id = resp.get("ingestionJob", {}).get("ingestionJobId", "unknown")
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
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
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
            return response(200, {"challenge": body.get("challenge")})

        # ------------------------------------------------------------------
        # STEP 3 — Verify Slack signature
        # ------------------------------------------------------------------
        logger.info("[step=3] Verifying Slack signature — request_id=%s", request_id)
        if not verify_slack_signature(headers, raw_body, SLACK_SIGNING_SECRET):
            logger.warning(
                "[step=3] Rejecting request — invalid Slack signature "
                "request_id=%s", request_id,
            )
            return response(403, {"error": "Invalid Slack signature"})

        logger.info("[step=3] Slack signature verified — request_id=%s", request_id)

        # Slack redelivers events not acked within ~3s; the timeline append
        # below is not idempotent, so drop retry deliveries outright.
        if "x-slack-retry-num" in headers:
            logger.info(
                "[step=3] Ignoring Slack retry delivery #%s — request_id=%s",
                headers["x-slack-retry-num"], request_id,
            )
            return response(200, {"message": "Retry ignored"})

        # ------------------------------------------------------------------
        # STEP 4 — Filter events (only plain user messages)
        # ------------------------------------------------------------------
        slack_event      = body.get("event", {})
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
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                },
                "incident":  incident,
                "timeline":  [],
                "document":  "",
            }
            logger.info(
                "[step=6] Creating new thread document — request_id=%s "
                "key=%s parsed_header=%s",
                request_id, s3_key, bool(incident.get("organization")),
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
        logger.info(
            "[step=7] Appended message to timeline — request_id=%s "
            "role=%s user_id=%s messages=%d",
            request_id, role, slack_event.get("user"),
            len(thread_doc["timeline"]),
        )

        # Regenerate document so the re-ingested embedding covers the full timeline
        thread_doc["document"]                  = _build_document(incident, thread_doc["timeline"])
        thread_doc["_meta"]["last_updated"]     = datetime.now(timezone.utc).isoformat()
        thread_doc["_meta"]["message_count"]    = len(thread_doc["timeline"])

        # ------------------------------------------------------------------
        # STEP 8 — Write back to S3 (same key → newest version wins)
        # ------------------------------------------------------------------
        put_started = time.time()
        logger.info(
            "[step=8] Writing thread document to S3 — request_id=%s "
            "bucket=%s key=%s",
            request_id, S3_BUCKET_NAME, s3_key,
        )
        _s3().put_object(
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
        # STEP 9 — On a resolution signal, extract a structured case
        # (redacted, confidence-gated) and sync the Knowledge Base.
        # Raw thread docs are audit-only; only cases/ should be indexed.
        # ------------------------------------------------------------------
        if role == "resolution":
            logger.info(
                "[step=9] Resolution signal — running live extraction — "
                "request_id=%s", request_id,
            )
            _extract_and_index(channel_id, thread_ts, thread_doc)

        # ------------------------------------------------------------------
        # STEP 10 — Return success
        # ------------------------------------------------------------------
        logger.info(
            "[step=10] Lambda invocation success — request_id=%s "
            "total_duration_ms=%d",
            request_id, int((time.time() - invocation_started) * 1000),
        )
        return response(200, {"message": "Event stored successfully"})

    except json.JSONDecodeError:
        logger.exception("Failed to parse request body as JSON — request_id=%s", request_id)
        return response(400, {"error": "Invalid JSON body"})
    except ClientError:
        logger.exception("AWS client error while handling event — request_id=%s", request_id)
        return response(500, {"error": "Failed to store event"})
    except Exception:
        logger.exception("Unexpected error in Lambda handler — request_id=%s", request_id)
        return response(500, {"error": "Internal server error"})
    finally:
        logger.info(
            "Lambda invocation end — request_id=%s total_duration_ms=%d",
            request_id, int((time.time() - invocation_started) * 1000),
        )
