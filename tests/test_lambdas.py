"""Lambda-track tests — pure local, no network, no AWS credentials needed.

Covers the shared Slack signature verification and the pure parsing/classifying
helpers in the live ingestion handler.
"""
import hashlib
import hmac
import time

from oncall.lambdas.post_events import (
    _build_document,
    _classify_role,
    _parse_incident_thread,
)
from oncall.lambdas.questions import _format_slack_message
from oncall.lambdas.slack_verify import response, verify_slack_signature

SECRET = "test-signing-secret"


def _sign(raw_body: str, timestamp: str, secret: str = SECRET) -> str:
    digest = hmac.new(
        secret.encode(), f"v0:{timestamp}:{raw_body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"v0={digest}"


def _headers(raw_body: str, timestamp: str | None = None, signature: str | None = None):
    ts = timestamp if timestamp is not None else str(int(time.time()))
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": signature if signature is not None else _sign(raw_body, ts),
    }


class TestVerifySlackSignature:
    def test_accepts_valid_signature(self):
        body = '{"type":"event_callback"}'
        assert verify_slack_signature(_headers(body), body, SECRET) is True

    def test_rejects_wrong_secret(self):
        body = '{"type":"event_callback"}'
        headers = _headers(body)
        assert verify_slack_signature(headers, body, "other-secret") is False

    def test_rejects_tampered_body(self):
        body = '{"type":"event_callback"}'
        headers = _headers(body)
        assert verify_slack_signature(headers, body + "x", SECRET) is False

    def test_rejects_missing_headers(self):
        assert verify_slack_signature({}, "body", SECRET) is False

    def test_rejects_stale_timestamp(self):
        body = "body"
        stale = str(int(time.time()) - 600)  # 10 minutes old
        assert verify_slack_signature(_headers(body, timestamp=stale), body, SECRET) is False

    def test_rejects_non_numeric_timestamp(self):
        body = "body"
        headers = _headers(body, timestamp="not-a-number")
        assert verify_slack_signature(headers, body, SECRET) is False


class TestResponse:
    def test_shapes_lambda_proxy_response(self):
        out = response(403, {"error": "nope"})
        assert out["statusCode"] == 403
        assert out["headers"]["Content-Type"] == "application/json"
        assert '"nope"' in out["body"]


class TestParseIncidentThread:
    HEADER = "acme (us-east-2/prod/loyalty-eks) | (v1.2.3) points-svc (grpc) - failed operation credit to wallet-svc"

    def test_parses_structured_header(self):
        out = _parse_incident_thread(self.HEADER)
        assert out is not None
        assert out["organization"] == "acme"
        assert out["region"] == "us-east-2"
        assert out["environment"] == "prod"
        assert out["cluster"] == "loyalty-eks"
        assert out["service_name"] == "points-svc"
        assert out["service_tag"] == "v1.2.3"
        assert out["service_protocol"] == "grpc"
        assert out["operation"] == "credit"
        assert out["target_service"] == "wallet-svc"
        assert "points-svc" in out["document"]

    def test_freeform_text_returns_none(self):
        assert _parse_incident_thread("pods are crashlooping again, anyone around?") is None

    def test_issue_without_operation(self):
        out = _parse_incident_thread(
            "acme (eu-west-1/staging/core) | (abc123) cart-svc (http) - readiness probe failing"
        )
        assert out is not None
        assert out["operation"] is None
        assert out["target_service"] is None
        assert out["issue_summary"] == "readiness probe failing"


class TestClassifyRole:
    def test_root_is_alert(self):
        assert _classify_role("anything at all", is_root=True) == "alert"

    def test_resolution_words(self):
        assert _classify_role("this is now resolved", is_root=False) == "resolution"

    def test_action_words(self):
        assert _classify_role("did a rollback to v1.2.2", is_root=False) == "action"

    def test_investigation_words(self):
        assert _classify_role("investigating the spike", is_root=False) == "investigation"

    def test_default_is_update(self):
        assert _classify_role("thanks for the heads up", is_root=False) == "update"


class TestBuildDocument:
    def test_includes_base_and_timeline(self):
        incident = {"document": "Production incident in acme."}
        timeline = [
            {"role": "alert", "text": "points-svc is down"},
            {"role": "resolution", "text": "restored after rollback"},
        ]
        doc = _build_document(incident, timeline)
        assert doc.startswith("Production incident in acme")
        assert "[alert] points-svc is down" in doc
        assert "[resolution] restored after rollback" in doc


class TestFormatSlackMessage:
    def test_includes_answer_and_citation_count(self):
        msg = _format_slack_message("Roll back the deploy.", 3)
        assert "Roll back the deploy." in msg
        assert "3 past incident(s) referenced" in msg
