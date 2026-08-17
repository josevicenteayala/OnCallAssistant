"""Shared Slack request verification + HTTP response helpers for the Lambdas.

Both handlers receive Slack callbacks over a public URL, so every request must
be authenticated with Slack's HMAC signing scheme before it is trusted.
"""
import hashlib
import hmac
import json
import logging
import time

logger = logging.getLogger(__name__)


def verify_slack_signature(headers: dict, raw_body: str, signing_secret: str) -> bool:
    """Return True only if the request carries a valid, fresh Slack signature.

    `headers` must already be lower-cased.
    """
    timestamp = headers.get("x-slack-request-timestamp", "")
    slack_signature = headers.get("x-slack-signature", "")

    if not timestamp or not slack_signature:
        logger.warning("Missing Slack signature headers")
        return False

    # Reject requests older than 5 minutes to prevent replay attacks
    try:
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("Request timestamp too old — possible replay attack")
            return False
    except ValueError:
        logger.warning("Invalid x-slack-request-timestamp value: %s", timestamp)
        return False

    base_string = f"v0:{timestamp}:{raw_body}"
    computed = hmac.new(
        signing_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(f"v0={computed}", slack_signature):
        logger.warning("Slack signature mismatch")
        return False

    return True


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
