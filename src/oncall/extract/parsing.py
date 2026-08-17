"""Defensive parsing of the extraction model's JSON output.

Shared by the batch CLI (extract.py) and the live ingestion Lambda
(oncall.lambdas.live_extract). Pure functions, no network.
"""
import json

REQUIRED_FIELDS = {
    "is_resolved", "summary", "issue", "affected_service", "category", "tags",
    "root_cause", "troubleshooting_steps", "solution", "solution_type",
    "confidence", "permalink", "redaction_applied",
}


def strip_fences(s: str) -> str:
    """Remove ```json ... ``` fences if the model added them."""
    s = s.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.removesuffix("```")
    return s.strip()


def parse_case(text: str):
    """Best-effort parse of the model output into a dict, or None."""
    candidate = strip_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Fall back to the outermost braces.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None
