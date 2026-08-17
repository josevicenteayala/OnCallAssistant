"""
bedrock_query_handler.py

AWS Lambda handler for the on-call Slack bot.
When a user mentions the bot in a Slack thread the handler:
  1. Verifies the request came from Slack
  2. Reads the parent thread message as incident context
  3. Enriches the question with that context
  4. Queries an Amazon Bedrock Knowledge Base
  5. Replies in the same Slack thread

Required environment variables:
  SLACK_SIGNING_SECRET  – used to verify Slack HMAC signatures
  SLACK_BOT_TOKEN       – xoxb-... token for posting messages
  BEDROCK_KB_ID         – Bedrock Knowledge Base ID
  BEDROCK_MODEL_ARN     – Claude model ARN for retrieve_and_generate
  AWS_REGION_NAME       – AWS region (e.g. us-east-1)
"""

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment variables — module-level for Lambda warm-container reuse
# ---------------------------------------------------------------------------

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")
BEDROCK_KB_ID        = os.environ.get("BEDROCK_KB_ID", "")
BEDROCK_MODEL_ARN    = os.environ.get("BEDROCK_MODEL_ARN", "")
AWS_REGION_NAME      = os.environ.get("AWS_REGION_NAME", "us-east-2")

# ---------------------------------------------------------------------------
# AWS clients — initialised once per warm container
# ---------------------------------------------------------------------------

bedrock_client = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION_NAME)

# ---------------------------------------------------------------------------
# Bedrock prompt template
# ---------------------------------------------------------------------------

_BEDROCK_PROMPT = (
    "You are an expert on-call assistant for engineers resolving production incidents.\n\n"
    "An engineer is asking for help with a current production issue. "
    "Use ONLY the context from past Slack incidents retrieved below to help them.\n\n"
    "If the incident context is provided, use it to make your response more specific "
    "and relevant.\n\n"
    "If no relevant past incidents are found say:\n"
    '"I couldn\'t find similar past incidents in our history. '
    "Please check the runbooks or escalate.\"\n\n"
    "Keep your response concise, clear and actionable.\n"
    "Use bullet points for resolution steps.\n"
    "Maximum 5 bullet points.\n\n"
    "Current incident and question:\n"
    "$query$\n\n"
    "Retrieved past incidents:\n"
    "$search_results$"
)

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
# STEP 3 — Slack signature verification
# ---------------------------------------------------------------------------


def _verify_slack_signature(headers: dict, raw_body: str) -> bool:
    timestamp       = headers.get("x-slack-request-timestamp", "")
    slack_signature = headers.get("x-slack-signature", "")

    if not timestamp or not slack_signature:
        logger.warning("Missing Slack signature headers")
        return False

    # Reject requests older than 5 minutes (replay-attack prevention)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("Request timestamp too old — possible replay attack")
            return False
    except ValueError:
        logger.warning("Invalid x-slack-request-timestamp value: %s", timestamp)
        return False

    base_string = f"v0:{timestamp}:{raw_body}"
    computed    = hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(f"v0={computed}", slack_signature):
        logger.warning("Slack signature mismatch")
        return False

    return True


# ---------------------------------------------------------------------------
# STEP 7 — Fetch parent thread message from Slack
# ---------------------------------------------------------------------------


def _fetch_thread_context(channel: str, thread_ts: str) -> str | None:
    """Return the text of the first (parent) message in a thread, or None on error."""
    params  = urllib.parse.urlencode({"channel": channel, "ts": thread_ts, "limit": 1})
    url     = f"https://slack.com/api/conversations.replies?{params}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("Failed to fetch thread context: %s", exc)
        return None

    if not data.get("ok"):
        logger.warning("conversations.replies returned ok=false: %s", data.get("error"))
        return None

    messages = data.get("messages", [])
    if not messages:
        logger.warning("conversations.replies returned no messages")
        return None

    return messages[0].get("text", "")


# ---------------------------------------------------------------------------
# STEP 8 — Query Bedrock Knowledge Base
# ---------------------------------------------------------------------------


def _query_bedrock(enriched_question: str) -> tuple[str, int]:
    """Return (answer_text, citation_count). Raises on Bedrock errors."""
    logger.info(
        "Calling Bedrock KB — kb_id=%s question_length=%d\n--- QUERY ---\n%s\n-------------",
        BEDROCK_KB_ID,
        len(enriched_question),
        enriched_question,
    )

    response = bedrock_client.retrieve_and_generate(
        input={"text": enriched_question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": BEDROCK_KB_ID,
                "modelArn": BEDROCK_MODEL_ARN,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": 5,
                    }
                },
                "generationConfiguration": {
                    "promptTemplate": {
                        "textPromptTemplate": _BEDROCK_PROMPT,
                    }
                },
            },
        },
    )

    answer    = response.get("output", {}).get("text", "No answer returned.")
    citations = response.get("citations", [])

    # Log each retrieved passage so you can verify what the KB returned
    for idx, citation in enumerate(citations, start=1):
        for ref in citation.get("retrievedReferences", []):
            content  = ref.get("content", {}).get("text", "")
            location = ref.get("location", {})
            score    = ref.get("score", "n/a")
            logger.info(
                "Retrieved passage [%d/%d] score=%s location=%s\n%s",
                idx,
                len(citations),
                score,
                location,
                content,
            )

    logger.info(
        "LLM answer (citations=%d answer_length=%d)\n--- ANSWER ---\n%s\n--------------",
        len(citations),
        len(answer),
        answer,
    )
    return answer, len(citations)


# ---------------------------------------------------------------------------
# STEP 9 — Format Slack reply
# ---------------------------------------------------------------------------


def _format_slack_message(answer: str, citation_count: int) -> str:
    return (
        ":robot_face: *On-Call Assistant*\n\n"
        ":mag: *Based on past incidents:*\n\n"
        f"{answer}\n\n"
        f":file_folder: *{citation_count} past incident(s) referenced*\n"
        "_Use /kb-search for more details_"
    )


# ---------------------------------------------------------------------------
# STEP 10 — Post message to Slack thread
# ---------------------------------------------------------------------------


def _post_to_slack(channel: str, thread_ts: str, text: str) -> None:
    payload = json.dumps(
        {"channel": channel, "thread_ts": thread_ts, "text": text}
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.error("Failed to call chat.postMessage: %s", exc)
        return

    if data.get("ok"):
        logger.info("Slack message posted successfully to channel=%s thread=%s", channel, thread_ts)
    else:
        logger.warning("Slack chat.postMessage returned ok=false: %s", data.get("error"))


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    # ------------------------------------------------------------------
    # STEP 1 — Parse Lambda Function URL event
    # ------------------------------------------------------------------
    headers  = {k.lower(): v for k, v in event.get("headers", {}).items()}
    raw_body = event.get("body", "") or ""

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse request body: %s", exc)
        return _response(400, {"error": "Invalid JSON body"})

    # ------------------------------------------------------------------
    # STEP 2 — Handle URL verification challenge (no auth needed)
    # ------------------------------------------------------------------
    if body.get("type") == "url_verification":
        logger.info("Responding to Slack URL verification challenge")
        return _response(200, {"challenge": body.get("challenge")})

    # ------------------------------------------------------------------
    # STEP 3 — Verify Slack request signature
    # ------------------------------------------------------------------
    if not _verify_slack_signature(headers, raw_body):
        return _response(403, {"error": "Invalid Slack signature"})

    # ------------------------------------------------------------------
    # STEP 4 — Filter: only process app_mention event callbacks
    # ------------------------------------------------------------------
    if body.get("type") != "event_callback":
        logger.info("Ignoring non-event_callback type=%s", body.get("type"))
        return _response(200, {"message": "Ignored"})

    slack_event = body.get("event", {})
    if slack_event.get("type") != "app_mention":
        logger.info("Ignoring non-app_mention event type=%s", slack_event.get("type"))
        return _response(200, {"message": "Ignored"})

    # ------------------------------------------------------------------
    # STEP 5 — Extract event details
    # ------------------------------------------------------------------
    text      = slack_event.get("text", "")
    channel   = slack_event.get("channel", "")
    ts        = slack_event.get("ts", "")
    thread_ts = slack_event.get("thread_ts")

    # reply_ts determines which thread to reply into
    reply_ts = thread_ts if thread_ts else ts

    logger.info("app_mention received — channel=%s ts=%s thread_ts=%s", channel, ts, thread_ts)

    # ------------------------------------------------------------------
    # STEP 6 — Strip the bot mention to get the clean question
    # ------------------------------------------------------------------
    cleaned_question = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if not cleaned_question:
        logger.info("Empty question after stripping mention — ignoring")
        return _response(200, {"message": "Empty question"})

    logger.info("Question extracted: %s", cleaned_question)

    # ------------------------------------------------------------------
    # STEP 7 — Fetch thread context (incident name / parent alert)
    # ------------------------------------------------------------------
    enriched_question = cleaned_question

    if thread_ts:
        incident_context = _fetch_thread_context(channel, thread_ts)
        if incident_context:
            logger.info("Thread context found (%d chars)", len(incident_context))
            enriched_question = (
                "Incident context from thread:\n"
                f"{incident_context}\n\n"
                "Engineer question:\n"
                f"{cleaned_question}"
            )
        else:
            logger.warning("Could not retrieve thread context — using question only")
    else:
        logger.info("Not inside a thread — no incident context available")

    logger.info("Enriched question built (%d chars)", len(enriched_question))

    # ------------------------------------------------------------------
    # STEP 8 — Query Bedrock Knowledge Base
    # ------------------------------------------------------------------
    try:
        answer, citation_count = _query_bedrock(enriched_question)
    except Exception as exc:  # noqa: BLE001
        logger.error("Bedrock query failed: %s", exc)
        _post_to_slack(
            channel,
            reply_ts,
            "Sorry, I couldn't query past incidents right now. Please try again in a moment.",
        )
        return _response(200, {"message": "Bedrock error — fallback message sent"})

    # ------------------------------------------------------------------
    # STEP 9 — Format the Slack reply
    # ------------------------------------------------------------------
    slack_message = _format_slack_message(answer, citation_count)

    # ------------------------------------------------------------------
    # STEP 10 — Post the response to the Slack thread
    # ------------------------------------------------------------------
    _post_to_slack(channel, reply_ts, slack_message)

    # ------------------------------------------------------------------
    # STEP 11 — Always return 200 to prevent Slack retry loops
    # ------------------------------------------------------------------
    return _response(200, {"message": "Bot response sent"})
 