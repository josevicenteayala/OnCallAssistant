"""Retrieval tests — pure local, no network."""
from oncall.prompts import build_answer_user_message
from oncall.retrieval.store import cosine_topk


def test_cosine_topk_orders_by_similarity():
    items = [
        {"id": "a", "vector": [1, 0, 0]},
        {"id": "b", "vector": [0, 1, 0]},
        {"id": "c", "vector": [0.9, 0.1, 0]},
    ]
    out = cosine_topk([1, 0, 0], items, k=2)
    assert [it["id"] for it, _ in out] == ["a", "c"]
    assert out[0][1] > out[1][1]


def test_cosine_topk_empty():
    assert cosine_topk([1, 0], [], k=3) == []


def test_answer_prompt_includes_case_fields():
    cases = [{
        "similarity": 0.81, "confidence": 0.9, "affected_service": "loyalty-points-svc",
        "category": "argocd_deployment", "issue": "CrashLoopBackOff after deploy",
        "root_cause": "missing REDIS_URL", "solution": "rolled back the argocd app",
        "permalink": "https://x.slack.com/p1",
    }]
    msg = build_answer_user_message("pods crashlooping", cases)
    assert "pods crashlooping" in msg
    assert "loyalty-points-svc" in msg
    assert "https://x.slack.com/p1" in msg
    assert "[1]" in msg


def test_answer_prompt_handles_no_cases():
    msg = build_answer_user_message("something novel", [])
    assert "no cases retrieved" in msg
