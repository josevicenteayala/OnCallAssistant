"""Held-out evaluation tests — pure local, mocked Bedrock where needed."""
from unittest.mock import MagicMock, patch

from oncall.eval.holdout import (
    hit_rate,
    judge,
    select_holdout,
    split_index,
)
from oncall.prompts import build_judge_user_message


def _case(ts, conf=0.9, resolved=True, issue="pods crashlooping", permalink=None):
    return {
        "thread_ts": ts,
        "confidence": conf,
        "is_resolved": resolved,
        "issue": issue,
        "root_cause": "bad deploy",
        "solution": "rollback",
        "permalink": permalink or f"https://x.slack.com/{ts}",
    }


class TestSelectHoldout:
    def test_picks_most_recent_indexable(self):
        cases = [_case("100.0"), _case("300.0"), _case("200.0")]
        out = select_holdout(cases, n=2)
        assert [c["thread_ts"] for c in out] == ["300.0", "200.0"]

    def test_filters_unresolved_low_confidence_and_missing_issue(self):
        cases = [
            _case("400.0", resolved=False),
            _case("300.0", conf=0.1),
            _case("200.0", issue=""),
            _case("100.0"),
        ]
        out = select_holdout(cases, n=10)
        assert [c["thread_ts"] for c in out] == ["100.0"]


class TestSplitIndex:
    def test_removes_heldout_by_permalink(self):
        items = [{"permalink": "a"}, {"permalink": "b"}, {"permalink": "c"}]
        holdout = [_case("1.0", permalink="b")]
        assert split_index(items, holdout) == [{"permalink": "a"}, {"permalink": "c"}]


class TestHitRate:
    def test_rate(self):
        assert hit_rate([{"hit": True}, {"hit": False}, {"hit": True}, {"hit": True}]) == 0.75

    def test_empty_is_zero(self):
        assert hit_rate([]) == 0.0


class TestJudge:
    def test_no_leads_is_a_miss_without_calling_the_model(self):
        client = MagicMock()
        out = judge(client, "model-x", _case("1.0"), leads=[])
        assert out["hit"] is False
        client.converse.assert_not_called()

    @patch("oncall.eval.holdout.converse")
    def test_parses_judge_verdict(self, mock_converse):
        mock_converse.return_value = '{"hit": true, "reason": "lead 1 names the same rollback"}'
        out = judge(MagicMock(), "model-x", _case("1.0"),
                    leads=[{"similarity": 0.8, "issue": "x", "solution": "rollback"}])
        assert out == {"hit": True, "reason": "lead 1 names the same rollback"}

    @patch("oncall.eval.holdout.converse")
    def test_unparseable_verdict_counts_as_miss(self, mock_converse):
        mock_converse.return_value = "I think it is probably fine"
        out = judge(MagicMock(), "model-x", _case("1.0"),
                    leads=[{"similarity": 0.8, "issue": "x", "solution": "y"}])
        assert out["hit"] is False


class TestJudgeUserMessage:
    def test_includes_incident_resolution_and_leads(self):
        msg = build_judge_user_message(
            "pods crashlooping", "missing env var", "rolled back",
            [{"similarity": 0.72, "affected_service": "points-svc",
              "issue": "CrashLoopBackOff", "root_cause": "bad config",
              "solution": "revert"}],
        )
        assert "pods crashlooping" in msg
        assert "missing env var" in msg
        assert "rolled back" in msg
        assert "[1] similarity=0.72" in msg
        assert "points-svc" in msg

    def test_no_leads_marker(self):
        msg = build_judge_user_message("issue", None, None, [])
        assert "(no leads retrieved)" in msg
