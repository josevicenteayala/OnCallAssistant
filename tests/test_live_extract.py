"""Live-extraction tests — mocked Bedrock, no network."""
import json
from unittest.mock import patch

from oncall.lambdas import live_extract, post_events


def _converse_response(payload: dict | str):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"output": {"message": {"content": [{"text": text}]}}}


FULL_CASE = {
    "is_resolved": True,
    "summary": "points-svc crashlooped after deploy; rolled back",
    "issue": "CrashLoopBackOff after deploy",
    "affected_service": "points-svc",
    "category": "argocd_deployment",
    "tags": ["kubernetes"],
    "root_cause": "missing REDIS_URL",
    "troubleshooting_steps": ["checked pod logs"],
    "solution": "rolled back the argocd app",
    "solution_type": "fix",
    "confidence": 0.9,
    "permalink": "model-suggested",
    "redaction_applied": False,
}

THREAD_DOC = {
    "timeline": [
        {"ts": "1.0", "user_id": "U1", "text": "points-svc is down", "role": "alert"},
        {"ts": "2.0", "user_id": "U2", "text": "resolved by rollback", "role": "resolution"},
    ]
}


class TestThreadToMessages:
    def test_maps_timeline_to_normalized_shape(self):
        msgs = live_extract.thread_to_messages(THREAD_DOC)
        assert msgs == [
            {"author": "U1", "ts": "1.0", "text": "points-svc is down"},
            {"author": "U2", "ts": "2.0", "text": "resolved by rollback"},
        ]

    def test_missing_user_becomes_unknown(self):
        msgs = live_extract.thread_to_messages(
            {"timeline": [{"ts": "1.0", "user_id": None, "text": "alert fired"}]}
        )
        assert msgs[0]["author"] == "unknown"


class TestExtractCase:
    @patch.object(live_extract, "BEDROCK_MODEL_ID", "model-x")
    @patch.object(live_extract, "_runtime")
    def test_returns_case_and_pins_permalink(self, mock_runtime):
        mock_runtime.return_value.converse.return_value = _converse_response(FULL_CASE)
        case = live_extract.extract_case(THREAD_DOC, "https://x.slack.com/p1")
        assert case is not None
        assert case["permalink"] == "https://x.slack.com/p1"  # not the model's value
        call = mock_runtime.return_value.converse.call_args[1]
        assert call["modelId"] == "model-x"
        assert call["inferenceConfig"]["temperature"] == 0

    @patch.object(live_extract, "BEDROCK_MODEL_ID", "model-x")
    @patch.object(live_extract, "_runtime")
    def test_unparseable_output_returns_none(self, mock_runtime):
        mock_runtime.return_value.converse.return_value = _converse_response("not json at all")
        assert live_extract.extract_case(THREAD_DOC, "p") is None

    @patch.object(live_extract, "BEDROCK_MODEL_ID", "model-x")
    @patch.object(live_extract, "_runtime")
    def test_missing_required_fields_returns_none(self, mock_runtime):
        mock_runtime.return_value.converse.return_value = _converse_response(
            {"is_resolved": True, "summary": "too sparse"}
        )
        assert live_extract.extract_case(THREAD_DOC, "p") is None

    @patch.object(live_extract, "BEDROCK_MODEL_ID", "")
    def test_no_model_id_skips_extraction(self):
        assert live_extract.extract_case(THREAD_DOC, "p") is None


class TestShouldIndex:
    def test_resolved_and_confident(self):
        assert live_extract.should_index({"is_resolved": True, "confidence": 0.9})

    def test_unresolved_is_gated(self):
        assert not live_extract.should_index({"is_resolved": False, "confidence": 0.9})

    def test_low_confidence_is_gated(self):
        assert not live_extract.should_index({"is_resolved": True, "confidence": 0.2})


class TestPermalink:
    @patch.object(post_events, "SLACK_WORKSPACE_URL", "https://acme.slack.com")
    def test_archive_url_when_workspace_configured(self):
        assert (
            post_events._permalink("C123", "1723456789.000200")
            == "https://acme.slack.com/archives/C123/p1723456789000200"
        )

    @patch.object(post_events, "SLACK_WORKSPACE_URL", "")
    def test_fallback_app_link(self):
        assert post_events._permalink("C123", "1.2") == "slack://channel/C123/1.2"


class TestExtractAndIndex:
    @patch.object(post_events, "_trigger_bedrock_sync")
    @patch.object(post_events, "_s3")
    @patch.object(post_events.live_extract, "extract_case")
    def test_indexable_case_is_stored_and_synced(self, mock_extract, mock_s3, mock_sync):
        mock_extract.return_value = dict(FULL_CASE)
        post_events._extract_and_index("C123", "1.2", THREAD_DOC)

        put = mock_s3.return_value.put_object.call_args[1]
        assert put["Key"].startswith(post_events.S3_CASES_PREFIX)
        stored = json.loads(put["Body"])
        assert stored["thread_ts"] == "1.2"
        assert stored["solution"] == "rolled back the argocd app"
        mock_sync.assert_called_once()

    @patch.object(post_events, "_trigger_bedrock_sync")
    @patch.object(post_events, "_s3")
    @patch.object(post_events.live_extract, "extract_case")
    def test_gated_case_is_not_stored(self, mock_extract, mock_s3, mock_sync):
        gated = dict(FULL_CASE, confidence=0.1)
        mock_extract.return_value = gated
        post_events._extract_and_index("C123", "1.2", THREAD_DOC)
        mock_s3.return_value.put_object.assert_not_called()
        mock_sync.assert_not_called()

    @patch.object(post_events, "_trigger_bedrock_sync")
    @patch.object(post_events, "_s3")
    @patch.object(post_events.live_extract, "extract_case")
    def test_failed_extraction_is_non_fatal(self, mock_extract, mock_s3, mock_sync):
        mock_extract.side_effect = RuntimeError("bedrock down")
        post_events._extract_and_index("C123", "1.2", THREAD_DOC)  # must not raise
        mock_s3.return_value.put_object.assert_not_called()
        mock_sync.assert_not_called()


class TestHandlerRetrySkip:
    def test_slack_retry_delivery_is_dropped(self):
        body = json.dumps({"type": "event_callback", "event": {"type": "message"}})
        with patch.object(post_events, "verify_slack_signature", return_value=True):
            out = post_events.lambda_handler(
                {
                    "headers": {
                        "x-slack-retry-num": "1",
                        "x-slack-request-timestamp": "1",
                        "x-slack-signature": "v0=x",
                    },
                    "body": body,
                },
                None,
            )
        assert out["statusCode"] == 200
        assert "Retry ignored" in out["body"]


class TestBatchParityWithSharedParsing:
    def test_extract_cli_uses_same_parser(self):
        # Guard against the batch CLI and the live path drifting apart.
        from oncall.extract import extract, parsing

        assert extract.parse_case is parsing.parse_case
        assert extract.REQUIRED_FIELDS is parsing.REQUIRED_FIELDS
        assert live_extract.parse_case is parsing.parse_case
