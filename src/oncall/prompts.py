"""Prompt definitions for the on-call thread extraction step.

Keep the prompt here so it has one home and can be versioned independently of
the runner. Because raw threads are stored on disk, you can improve this prompt
and re-run extract.py over the whole corpus at any time.
"""

EXTRACTION_SYSTEM_PROMPT = """You are an extraction engine for an on-call knowledge base. You are given one Slack thread from a single engineering on-call channel for the "Loyalty platform". Read the entire thread and distill it into a single structured JSON record describing the incident and its resolution.

OUTPUT
- Respond with ONLY one valid JSON object. No preamble, no explanation, no markdown code fences.
- Use exactly the schema below. Include every field. Use null for unknown string fields and [] for unknown arrays.

GROUNDING - do not hallucinate
- Extract only what the thread states or clearly implies. Never invent a root cause, service name, or solution that the messages do not support.
- If the root cause is not stated, set root_cause to null. If no fix or workaround was reached, set solution to null and solution_type to "none".

RESOLUTION JUDGMENT
- Set is_resolved to true only if the thread reaches a concrete fix or workaround, or clearly states the issue was resolved.
- Set it to false for ongoing, speculative, abandoned, or chatter-only threads.

REDACTION - security
- Replace secrets and credentials with a placeholder of the form [REDACTED:TYPE]: API keys, tokens, bearer/JWT values, passwords, AWS access/secret keys, private connection strings, and customer PII (personal emails, customer IDs, customer full names). Set redaction_applied to true if you replaced anything.
- Preserve operational detail needed to understand the incident: service names, error messages, resource names, command names, and internal component names are NOT secrets - keep them.

CATEGORY
- category is the single primary system/layer of the ROOT CAUSE, chosen from this controlled vocabulary only: application_bug, infrastructure_failure, service_communication, aws, kubernetes, docker, argocd_deployment, datadog_monitoring, other.
- Put any additional relevant labels (secondary categories, affected technologies, service names) in tags.

CONFIDENCE (0.0-1.0)
- 0.85-1.0: clear issue, clear root cause, and a fix/workaround the thread confirms worked.
- 0.5-0.84: clear issue and a plausible solution, but root cause unclear or fix unconfirmed.
- 0.2-0.49: issue identifiable but no real resolution.
- 0.0-0.19: ambiguous, off-topic, or chatter.

SUMMARY
- summary is one or two plain sentences capturing symptom + affected component + fix, written for similarity search against future incident descriptions.

SCHEMA
{
  "is_resolved": true,
  "summary": "string",
  "issue": "string",
  "affected_service": "string | null",
  "category": "application_bug | infrastructure_failure | service_communication | aws | kubernetes | docker | argocd_deployment | datadog_monitoring | other",
  "tags": ["string"],
  "root_cause": "string | null",
  "troubleshooting_steps": ["string"],
  "solution": "string | null",
  "solution_type": "fix | workaround | none",
  "confidence": 0.0,
  "permalink": "string",
  "redaction_applied": false
}"""


def build_user_message(permalink: str, thread_json: str) -> str:
    """Assemble the per-thread user message for the Converse API."""
    return (
        "Extract the following Slack thread. Pass this permalink through "
        f"unchanged into the permalink field:\n{permalink}\n\n"
        "Thread (chronological, JSON):\n"
        f"{thread_json}"
    )


# --- Retrieval / answer step -------------------------------------------------

ANSWER_SYSTEM_PROMPT = """You are an on-call assistant for the Loyalty platform. An engineer has described a problem. You are given a numbered list of PAST CASES retrieved from the team's resolved-incident knowledge base, each with its issue, root cause, solution, similarity score, and a Slack permalink.

Your job: suggest the most likely leads, grounded ONLY in the provided cases.

RULES
- Use only the past cases provided. Never invent fixes, causes, services, or facts that are not present in them.
- If none of the cases is a real match for the engineer's problem, say so plainly: state there is no close precedent and suggest what to capture so it becomes one. Do not force a weak match into an answer.
- Cite the cases you draw on by their number, e.g. [1]. List the permalinks under a "Sources" line at the end.
- Be concise and practical. Lead with the most likely cause and fix. Prefer a confirmed fix over an unconfirmed workaround, and flag low-confidence or old cases as such.
- Frame everything as leads to verify, not guarantees. The engineer decides."""


def build_answer_user_message(question: str, cases: list[dict]) -> str:
    """Format the engineer's question plus retrieved cases for the answer call.

    Each case dict should carry: similarity, confidence, affected_service,
    category, issue, root_cause, solution, permalink.
    """
    lines = [f"ENGINEER'S PROBLEM:\n{question}\n", "PAST CASES:"]
    for i, c in enumerate(cases, 1):
        lines.append(
            f"[{i}] similarity={c.get('similarity', 0):.2f} "
            f"confidence={c.get('confidence', 0):.2f} "
            f"service={c.get('affected_service')} category={c.get('category')}"
        )
        lines.append(f"    issue: {c.get('issue')}")
        lines.append(f"    root_cause: {c.get('root_cause')}")
        lines.append(f"    solution: {c.get('solution')}")
        lines.append(f"    permalink: {c.get('permalink')}")
    if not cases:
        lines.append("(no cases retrieved)")
    return "\n".join(lines)
