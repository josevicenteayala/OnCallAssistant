# On-Call Assistant — Thread Extraction Prompt (v1)

This is the prompt that turns one resolved Slack thread into one structured case record. It is the quality bottleneck of the whole system: every downstream answer is only as good as this extraction. Run it **one thread per call** on Amazon Bedrock at **temperature 0**.

Because the raw threads live in S3, this prompt is re-runnable — improve it and reprocess the whole corpus anytime.

---

## System prompt

> You are an extraction engine for an on-call knowledge base. You are given one Slack thread from a single engineering on-call channel for the "Loyalty platform". Read the entire thread and distill it into a single structured JSON record describing the incident and its resolution.
>
> **OUTPUT**
> - Respond with ONLY one valid JSON object. No preamble, no explanation, no markdown code fences.
> - Use exactly the schema below. Include every field. Use `null` for unknown string fields and `[]` for unknown arrays.
>
> **GROUNDING — do not hallucinate**
> - Extract only what the thread states or clearly implies. Never invent a root cause, service name, or solution that the messages do not support.
> - If the root cause is not stated, set `root_cause` to `null`. If no fix or workaround was reached, set `solution` to `null` and `solution_type` to `"none"`.
>
> **RESOLUTION JUDGMENT**
> - Set `is_resolved` to `true` only if the thread reaches a concrete fix or workaround, or clearly states the issue was resolved.
> - Set it to `false` for ongoing, speculative, abandoned, or chatter-only threads.
>
> **REDACTION — security**
> - Replace secrets and credentials with a placeholder of the form `[REDACTED:TYPE]`: API keys, tokens, bearer/JWT values, passwords, AWS access/secret keys, private connection strings, and customer PII (personal emails, customer IDs, customer full names). Set `redaction_applied` to `true` if you replaced anything.
> - Preserve operational detail needed to understand the incident: service names, error messages, resource names, command names, and internal component names are NOT secrets — keep them.
>
> **CATEGORY**
> - `category` is the single primary system/layer of the ROOT CAUSE, chosen from this controlled vocabulary only: `application_bug`, `infrastructure_failure`, `service_communication`, `aws`, `kubernetes`, `docker`, `argocd_deployment`, `datadog_monitoring`, `other`.
> - Put any additional relevant labels (secondary categories, affected technologies, service names) in `tags`.
>
> **CONFIDENCE (0.0–1.0)**
> - `0.85–1.0`: clear issue, clear root cause, and a fix/workaround the thread confirms worked.
> - `0.5–0.84`: clear issue and a plausible solution, but root cause unclear or fix unconfirmed.
> - `0.2–0.49`: issue identifiable but no real resolution.
> - `0.0–0.19`: ambiguous, off-topic, or chatter.
>
> **SUMMARY**
> - `summary` is one or two plain sentences capturing symptom + affected component + fix, written for similarity search against future incident descriptions.
>
> **SCHEMA**
> ```json
> {
>   "is_resolved": true,
>   "summary": "string",
>   "issue": "string",
>   "affected_service": "string | null",
>   "category": "application_bug | infrastructure_failure | service_communication | aws | kubernetes | docker | argocd_deployment | datadog_monitoring | other",
>   "tags": ["string"],
>   "root_cause": "string | null",
>   "troubleshooting_steps": ["string"],
>   "solution": "string | null",
>   "solution_type": "fix | workaround | none",
>   "confidence": 0.0,
>   "permalink": "string",
>   "redaction_applied": false
> }
> ```

---

## User-message template

> Extract the following Slack thread. Pass this permalink through unchanged into the `permalink` field:
> `{{PERMALINK}}`
>
> Thread (chronological, JSON):
> ```json
> {{NORMALIZED_THREAD_JSON}}
> ```

`{{NORMALIZED_THREAD_JSON}}` is the cleaned thread your ingestion Lambda produces — an array of messages with `author`, `ts`, and `text`, with raw @mention IDs already resolved to handles.

---

## Example 1 — clean resolution (high confidence)

**Input thread**
```json
{
  "permalink": "https://loyalty.slack.com/archives/C0XXX/p1699891234",
  "messages": [
    {"author": "alice", "ts": "1699891200", "text": "loyalty-points-svc pods are in CrashLoopBackOff in prod since the 14:02 deploy. anyone else seeing this?"},
    {"author": "bob",   "ts": "1699891260", "text": "kubectl logs shows 'panic: missing env REDIS_URL'. looks like the new release dropped it."},
    {"author": "alice", "ts": "1699891320", "text": "confirmed — values-prod.yaml lost the redis block in the last PR, and argocd synced the bad manifest."},
    {"author": "bob",   "ts": "1699891500", "text": "rolled back the argocd app to the previous revision, pods are healthy now. fixing values in a follow-up PR."},
    {"author": "alice", "ts": "1699891560", "text": "thanks, resolved. PR #1423 restores the redis env."}
  ]
}
```

**Expected output**
```json
{
  "is_resolved": true,
  "summary": "loyalty-points-svc crash-looped in production after a deploy dropped the REDIS_URL env var; fixed by rolling back the ArgoCD app and restoring the config.",
  "issue": "loyalty-points-svc pods entered CrashLoopBackOff in production immediately after the 14:02 deploy.",
  "affected_service": "loyalty-points-svc",
  "category": "argocd_deployment",
  "tags": ["kubernetes", "configuration", "redis"],
  "root_cause": "The latest release dropped the REDIS_URL environment variable: values-prod.yaml lost its redis configuration block in a PR, and ArgoCD synced the faulty manifest, causing the service to panic on startup.",
  "troubleshooting_steps": [
    "Observed pods in CrashLoopBackOff after the deploy",
    "Inspected pod logs with kubectl and found a panic for missing REDIS_URL",
    "Traced the missing variable to a removed redis block in values-prod.yaml",
    "Confirmed ArgoCD had synced the faulty manifest"
  ],
  "solution": "Rolled back the ArgoCD application to the previous revision to restore healthy pods, then opened follow-up PR #1423 to restore the REDIS_URL configuration in values-prod.yaml.",
  "solution_type": "fix",
  "confidence": 0.9,
  "permalink": "https://loyalty.slack.com/archives/C0XXX/p1699891234",
  "redaction_applied": false
}
```

Note how `category` reflects where the root cause sits (a bad ArgoCD-synced manifest) while the K8s symptom lands in `tags` — that keeps the controlled vocabulary clean for filtering while preserving the cross-cutting detail.

---

## Example 2 — unresolved / speculative (low confidence, gets dropped)

**Input thread**
```json
{
  "permalink": "https://loyalty.slack.com/archives/C0XXX/p1699950000",
  "messages": [
    {"author": "carol", "ts": "1699950000", "text": "seeing elevated p99 latency on the rewards API, ~2s, started around noon"},
    {"author": "dave",  "ts": "1699950120", "text": "datadog shows the spike but there's no deploy around then. maybe downstream?"},
    {"author": "carol", "ts": "1699950300", "text": "could be the partner API. keeping an eye on it for now"}
  ]
}
```

**Expected output**
```json
{
  "is_resolved": false,
  "summary": "Elevated p99 latency (~2s) on the rewards API starting around noon, with no deploy correlated; cause unconfirmed and unresolved.",
  "issue": "Elevated p99 latency of roughly 2 seconds on the rewards API, beginning around noon.",
  "affected_service": "rewards API",
  "category": "datadog_monitoring",
  "tags": ["latency", "service_communication", "partner_api"],
  "root_cause": null,
  "troubleshooting_steps": [
    "Confirmed the latency spike in Datadog",
    "Checked for a correlated deploy and found none",
    "Hypothesized a downstream/partner API cause; continued monitoring"
  ],
  "solution": null,
  "solution_type": "none",
  "confidence": 0.2,
  "permalink": "https://loyalty.slack.com/archives/C0XXX/p1699950000",
  "redaction_applied": false
}
```

---

## Wiring notes

- **One thread per call, temperature 0.** Deterministic output and no cross-thread contamination. A modest `max_tokens` (e.g. ~1,500) is plenty for one record.
- **Parse defensively.** Trim whitespace, strip stray code fences if the model adds them, then `JSON.parse`. On a parse failure, log the raw output and retry once; if it still fails, store the thread flagged `extraction_failed` for human review rather than dropping it silently.
- **What to index vs. drop.** Only index records where `is_resolved` is `true` **and** `confidence >= 0.4` (tune this on real data). Lower-confidence and unresolved records still go to DynamoDB flagged for review — they may be worth a human pass, and they tell you where the channel's knowledge is thin.
- **Redaction is layered.** This prompt is the first redaction pass at ingest; Bedrock Guardrails is the second pass at answer time. Don't rely on either alone.
- **Re-runnable by design.** Keep every raw thread in S3. When you improve this prompt, reprocess the whole corpus — the structured store and vector index are derived artifacts, never the source of truth.
- **Validate before trusting.** Run this over ~30 real threads first and eyeball the JSON. Tune the confidence rubric and the `category` definition to how your team actually talks before you back-fill all three years.

---

*v1. Five required fields (issue, affected_service, root_cause, troubleshooting_steps, solution) plus operational fields (is_resolved, summary, category, tags, solution_type, confidence, permalink, redaction_applied) for filtering, retrieval, gating, and citation.*
