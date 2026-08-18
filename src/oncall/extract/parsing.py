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
    """Best-effort parse of the model output into a dict, or None.

    Only dicts are ever returned: valid JSON that isn't an object (a list,
    string, number...) falls through to the brace-extraction attempt and then
    to None, so callers can safely use dict operations on the result.
    """
    candidate = strip_fences(text)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost braces.
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
