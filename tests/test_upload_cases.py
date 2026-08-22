"""Uploader tests — pure local, mocked S3."""
import json
from unittest.mock import patch

from oncall.publish.upload_cases import (
    case_key,
    channel_from_permalink,
    indexable,
    select_cases,
)


def _case(ts="1787153577.898639", conf=0.9, resolved=True,
          permalink="https://personalvin.slack.com/archives/C09126VCR1P/p1787153577898639"):
    return {
        "thread_ts": ts,
        "confidence": conf,
        "is_resolved": resolved,
        "permalink": permalink,
        "summary": "s",
    }


class TestChannelFromPermalink:
    def test_extracts_channel_id(self):
        assert channel_from_permalink(
            "https://personalvin.slack.com/archives/C09126VCR1P/p1787153577898639"
        ) == "C09126VCR1P"

    def test_app_link_fallback_returns_none(self):
        assert channel_from_permalink("slack://channel/C123/1.2") is None

    def test_none_is_safe(self):
        assert channel_from_permalink(None) is None


class TestCaseKey:
    def test_matches_live_path_layout(self):
        assert case_key("cases/", "C09126VCR1P", "1787153577.898639") == \
            "cases/C09126VCR1P/1787153577.898639.json"


class TestIndexableGate:
    def test_same_gate_as_index_step(self):
        assert indexable(_case(conf=0.4), 0.4)
        assert not indexable(_case(conf=0.39), 0.4)
        assert not indexable(_case(resolved=False), 0.4)


class TestSelectCases:
    def test_selects_and_reports_skips(self):
        cases = [
            _case(),                                   # uploadable
            _case(conf=0.1),                           # gated
            _case(resolved=False),                     # gated
            _case(permalink="slack://channel/C1/1.2"), # no channel, no fallback
        ]
        uploadable, gated, no_channel = select_cases(cases, 0.4, channel_fallback=None)
        assert len(uploadable) == 1
        assert gated == 2
        assert no_channel == 1
        channel, thread_ts, case = uploadable[0]
        assert channel == "C09126VCR1P"
        assert thread_ts == "1787153577.898639"
        assert case["summary"] == "s"

    def test_channel_fallback_rescues_unparsed_permalinks(self):
        cases = [_case(permalink="slack://channel/C1/1.2")]
        uploadable, _, no_channel = select_cases(cases, 0.4, channel_fallback="C09126VCR1P")
        assert no_channel == 0
        assert uploadable[0][0] == "C09126VCR1P"


class TestRun:
    def test_uploads_gated_cases_with_live_layout_keys(self, tmp_path):
        from oncall.publish import upload_cases

        cases_file = tmp_path / "cases.jsonl"
        cases_file.write_text(
            json.dumps(_case()) + "\n" + json.dumps(_case(conf=0.1)) + "\n"
        )
        with patch.object(upload_cases, "boto3") as mock_boto3:
            upload_cases.run(str(cases_file), "bucket-x", "cases/", 0.4,
                             None, dry_run=False, sync=False)
        put = mock_boto3.client.return_value.put_object.call_args[1]
        assert put["Bucket"] == "bucket-x"
        assert put["Key"] == "cases/C09126VCR1P/1787153577.898639.json"
        assert json.loads(put["Body"])["summary"] == "s"
        assert mock_boto3.client.return_value.put_object.call_count == 1

    def test_dry_run_touches_nothing(self, tmp_path):
        from oncall.publish import upload_cases

        cases_file = tmp_path / "cases.jsonl"
        cases_file.write_text(json.dumps(_case()) + "\n")
        with patch.object(upload_cases, "boto3") as mock_boto3:
            upload_cases.run(str(cases_file), "bucket-x", "cases/", 0.4,
                             None, dry_run=True, sync=False)
        mock_boto3.client.assert_not_called()
