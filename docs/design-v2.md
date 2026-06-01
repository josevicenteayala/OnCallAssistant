# AI-Powered On-Call Assistant — Design v2

*Loyalty Platform. Updated with confirmed project parameters. Cost figures are orders of magnitude — verify against current Bedrock pricing in your region before committing.*

---

## Confirmed parameters

| Parameter | Value | Design consequence |
|---|---|---|
| **Volume** | ~6 messages/day; 3 years to back-fill | Tiny corpus (~6,570 messages → a few hundred to ~2,000 cases). Cost is negligible; vector-store choice stops mattering for scale. |
| **Data residency** | Slack content may leave the AWS account | No VPC-only/PrivateLink requirement. Standard Bedrock setup is fine; VPC isolation is optional hardening, not a gate. |
| **Channels** | One channel | No source tagging or per-channel access control. Single ingestion source, single access scope. |
| **Autonomy** | Assistant auto-posts suggestions into threads | Highest-stakes requirement. Auto-posting is in the MVP, shipped in **shadow mode first**. Demands trigger detection, confidence gating, and trust controls. |
| **Integrations** | Datadog / ArgoCD = future evolution | Out of scope now; link-out deferred to a later phase. |

---

## 1. Project statement

The Loyalty on-call team's institutional knowledge — root causes, troubleshooting steps, and fixes for recurring AWS / Kubernetes / Docker / ArgoCD / Datadog issues — currently lives only as unstructured Slack history that is slow to search under incident pressure. We will build an AI-powered On-Call Assistant that ingests three years of one Slack channel into a structured, searchable knowledge base and uses retrieval-augmented generation to surface likely solutions for new issues. It will both answer engineers on demand and **proactively post grounded suggestions into new issue threads**, gated by a confidence threshold and rolled out via shadow mode. Success means a measurable drop in time-to-first-useful-lead for recurring incidents.

---

## 2. High-level architecture

Two pipelines share one knowledge base. The read path now has **two branches**: on-demand (engineer asks) and proactive (new message triggers an auto-suggestion).

```
                    SLACK WORKSPACE (single on-call channel)
                              │
        ┌─────────────────────┴──────────────────────┐
        │ (A) backfill: Web API conversations.history │  (one-time, 3 yrs)
        │ (B) live:     Events API / Socket Mode      │  (ongoing)
        └─────────────────────┬──────────────────────┘
                              ▼
                    [ Ingestion Lambda ]
              normalize threads → JSON, strip @mentions,
              attach metadata (service, author, ts, permalink)
                              │
                              ▼
                    [ S3 raw bucket ]  ──► (audit / replay source of truth)
                              │
                              ▼
              [ Extraction step — LLM via Bedrock ]
        per resolved thread → { issue, affected_service,
          root_cause, troubleshooting_steps, solution,
          confidence, links }   + PII/secret redaction
                              │
              ┌───────────────┴───────────────┐
              ▼                                ▼
   [ DynamoDB: structured cases ]    [ Bedrock Knowledge Base ]
   (filter, dedupe, status,          (chunk + embed + index into
    "verified" flags)                 S3 Vectors; managed RAG)
                                                 │
═════════════════════════════════════════════════════════════════
   READ PATH — two branches
                                                 │
   ┌─────────────────────────────┐   ┌──────────┴───────────────┐
   │ ON-DEMAND                   │   │ PROACTIVE (auto-post)     │
   │ engineer @-mentions bot     │   │ new message arrives       │
   │            │                │   │            │              │
   │            │                │   │   [ trigger classifier ]  │
   │            │                │   │   new issue? else drop    │
   │            ▼                │   │            ▼              │
   │      [ Orchestrator ] ◄─────┼───┼──── retrieve top-k (KB)   │
   │   + DynamoDB metadata filter│   │     + DynamoDB filter     │
   │            ▼                │   │            ▼              │
   │   [ Bedrock + Guardrails ]  │   │   [ confidence gate ]     │
   │   grounded answer + cites   │   │   above threshold?        │
   │            ▼                │   │      yes │   no → log     │
   │   reply in thread           │   │          ▼   (silent)     │
   └─────────────────────────────┘   │   post threaded reply,    │
                                      │   labeled AI suggestion   │
                                      │   (shadow → live)         │
                                      └───────────────────────────┘
```

**Write path.** A one-time backfill pulls 3 years via the Slack Web API; thereafter the Events API (or Socket Mode for the PoC) streams new messages. A Lambda normalizes each thread to JSON, stores the raw version in S3 (replay/audit source of truth), and triggers an LLM extraction step that distills each *resolved* thread into five structured fields plus metadata, with PII/secret redaction applied at this stage. Structured records go to DynamoDB; the same content is indexed into a Bedrock Knowledge Base backed by S3 Vectors.

**Read path — on-demand.** An engineer @-mentions the bot in a thread; the orchestrator retrieves similar past cases, optionally filters by service via DynamoDB, and returns a grounded answer with Slack permalinks.

**Read path — proactive.** Every new message passes a lightweight **trigger classifier** ("is this a new issue worth answering?"). If yes, the system retrieves candidates and applies a **confidence gate**; only suggestions clearing the threshold are posted as a labeled threaded reply. Everything else is logged silently. This branch ships in shadow mode (posts to a private review channel/DM) before going live.

**Core principle unchanged:** the model never answers from its own memory. Every suggestion cites a real prior thread so engineers can trust-but-verify under pressure.

---

## 3. Recommended technologies

| Capability | Recommendation | Alternative(s) | Rationale |
|---|---|---|---|
| **Slack ingestion** | Events API + Web API → API Gateway → Lambda | Socket Mode (PoC) | Events API is the production pattern; Socket Mode needs no public endpoint for the PoC. |
| **Raw storage / audit** | Amazon S3 | — | Cheap, durable; lets you re-run extraction when prompts improve. |
| **Extraction** | LLM on Amazon Bedrock | OSS model on SageMaker | Extraction is a prompt, not custom code; stays serverless. |
| **Embeddings** | Amazon Titan Text Embeddings v2 | Cohere Embed; OpenAI | Native to Bedrock Knowledge Bases. |
| **Vector store** | **Amazon S3 Vectors** | DynamoDB-with-vector-field; OpenSearch Serverless (only at scale) | No idle floor; pay-as-you-go. At ~2,000 vectors, cost is pennies. **Do not** use OpenSearch Serverless here — its standing floor dwarfs the workload. |
| **Managed RAG** | Amazon Bedrock Knowledge Bases | LangChain/LlamaIndex on Lambda | Removes chunk→embed→index→retrieve plumbing. |
| **LLM / inference** | Amazon Bedrock (Claude / Amazon Nova) | SageMaker OSS | Managed, pay-per-token; no VPC requirement given your data-residency answer. |
| **Trigger classifier** | Cheap Bedrock model call or heuristics | Comprehend custom classifier | Decides "new issue vs. chatter." Trivial load at 6 msgs/day. |
| **Orchestration** | Bedrock Agents (config-based) | AgentCore (later); Step Functions + Lambda | Fast to stand up; graduate to AgentCore for memory/policy/observability. |
| **Structured store** | Amazon DynamoDB | Aurora | Filtered lookups, dedupe, human-verification flags, kill-switch flag. |
| **Compute glue** | AWS Lambda | Fargate (long jobs) | Event-driven, scales to zero. |
| **Interface** | Slack bot (Bolt SDK) | small web UI | Engineers already live in Slack. |
| **Safety / governance** | Bedrock Guardrails | Comprehend PII pre-index | Redact secrets/PII at ingest and at answer time. |
| **Observability** | CloudWatch (+ Datadog later) | AgentCore Observability | Reuse existing Datadog dashboards in a later phase. |

### Cost estimate (orders of magnitude — verify before committing)

Volume math: 6 × 365 × 3 ≈ **6,570 messages** to back-fill, ~180/month ongoing, yielding **a few hundred to ~2,000 structured cases**.

- **One-time backfill:** thousands of short extraction calls + embedding a few thousand chunks → **tens of dollars, one-off**.
- **Vector store (S3 Vectors):** **pennies/month** — no idle floor.
- **Ongoing inference:** new-message classification + auto-post checks + Q&A → likely **under ~$20–30/month**, dominated by Bedrock tokens.
- **DynamoDB / Lambda / S3:** effectively free at this scale.

At this corpus size a heavy vector database earns nothing; RAG is justified here for grounding and citations, not scale.

---

## 4. Capability decision matrix (build-time vs runtime)

| Capability | Needed? | Layer | Why |
|---|---|---|---|
| GitHub Copilot skills | Optional | Build-time | Helps *us write* the Lambdas/IaC. Not part of the deployed system. |
| Copilot custom instructions | Optional | Build-time | Repo-level code conventions. Not part of the architecture. |
| AI agents | Yes (phased) | Runtime | Config-based Bedrock Agent for the MVP; AgentCore later. |
| RAG pipeline | **Core** | Runtime | The whole value proposition — grounding answers in prior cases. |
| Slack API integration | **Core** | Runtime | Both data source and interface; Web API (backfill), Events API/Socket Mode (live), Bolt (bot). |
| AWS Bedrock | **Core** | Runtime | Model + Knowledge Bases + Guardrails + Agents. The spine. |
| OpenSearch Serverless | **No (at this scale)** | Runtime | Standing cost floor is unjustifiable for ~2,000 vectors. Reconsider only if volume grows orders of magnitude. |
| AWS Lambda | Yes | Runtime | Glue for ingestion, extraction triggers, classifier, bot handler. |
| Amazon S3 | Yes | Runtime | Raw audit store and (as S3 Vectors) the vector store. |
| Amazon DynamoDB | Yes | Runtime | Structured cases, dedupe, verification flags, kill switch. |
| Bedrock Guardrails | Recommended | Runtime | PII/secret redaction + grounding — engineers paste credentials/IDs into Slack. |
| Trigger classifier | **Yes (new)** | Runtime | Required for auto-posting — decides what's worth answering. |
| API Gateway | Yes (prod) | Runtime | Webhook for Events API (skip for Socket Mode PoC). |

**One-liner:** Copilot and custom instructions are optional *developer-productivity* tools (build-time). The runtime system is **Slack API + Bedrock (Knowledge Bases + model + Guardrails + Agent) + S3 (incl. S3 Vectors) + DynamoDB + Lambda**, plus a lightweight trigger classifier for auto-posting.

---

## 5. Proof of Concept

**Hypothesis:** prior Slack threads can surface a useful lead for a new incident.

1. **Data:** export 3 years of the channel once via the Slack Web API.
2. **Process:** script normalizes threads → JSON in S3; a Bedrock prompt extracts the five fields per resolved thread.
3. **Index:** Bedrock Knowledge Base on S3 Vectors over the structured JSON.
4. **Interface:** a CLI/notebook (or Socket Mode bot) that takes a question, retrieves top-k, returns a grounded answer with permalinks.
5. **Evaluate:** hide the solutions on 20–30 recently resolved incidents and measure whether the assistant's suggestion would have pointed an engineer in the right direction. That hit-rate is your go/no-go.

No live ingestion, no auto-posting, no UI. One workflow: ask → retrieve → grounded answer.

---

## 6. MVP scope (demo)

**In scope**
- Live ingestion of new messages (Events API or Socket Mode).
- Automated extraction of the five fields on resolved threads, with PII/secret redaction.
- Knowledge Base on S3 Vectors with scheduled re-index.
- **On-demand bot:** @-mention → up to 3 candidate cases + permalinks + short synthesis.
- **Auto-posting in shadow mode:** trigger classifier + confidence gate; suggestions routed to a private review channel/DM, not the live channel, until accuracy is validated.
- 👍/👎 capture to DynamoDB; one-flag kill switch.

**Out of scope (deferred)**
- Flipping auto-post to the live channel (a Phase-2 toggle once shadow accuracy is proven).
- Datadog / ArgoCD / runbook integrations.
- Standalone web UI (Slack-only).
- Fine-tuning or custom-model hosting.
- Multi-channel support.

**Rough effort:** ~5–7 engineer-weeks for an AWS-familiar team, assuming Bedrock access is enabled and Socket Mode (no API Gateway hardening). The extra ~1 week over v1 is the trigger classifier, confidence gating, and shadow-mode plumbing. *Estimate — depends on your AWS footprint and approval cycles.*

---

## 7. Risks, limitations, security

| Risk | Mitigation |
|---|---|
| **Auto-posting a wrong fix during an incident** | Confidence gate tuned for precision over coverage; shadow mode before live; clear AI labeling; cite real permalinks; one suggestion per issue; kill switch. |
| **Noisy / spammy auto-posts** | Trigger classifier drops chatter, status updates, replies, and bot messages; post as threaded reply, never a new channel message; never reply to itself. |
| **Hallucinated / ungrounded answers** | Hard grounding — no retrieval hit, no answer; show retrieved cases alongside synthesis; Guardrails for grounding. |
| **Stale / superseded solutions** | Timestamp cases; weight recency; "outdated" flag; show thread date in every suggestion. |
| **Secrets / PII in the index** | Redact at ingest *and* via Guardrails at answer time; encrypt the index; restrict access. |
| **Slack rate limits / permissions** | Backfill with backoff inside tier limits; minimum OAuth scopes; tokens in Secrets Manager, never in code. |
| **Cost drift** | S3 Vectors (no floor); Bedrock token budgets/alerts; cap top-k and answer length. |
| **Over-trust / deskilling** | Frame output as "leads to investigate," keep humans in the loop, track 👍/👎. |
| **Garbage-in (unresolved threads)** | Index only resolved threads; extraction emits a confidence score and drops low-signal records. |

**Limitation to set expectations on:** the assistant is only as good as the channel's history, and three years of one low-traffic channel is a modest corpus. Novel, unprecedented incidents will get weak suggestions — expected, and the 👍/👎 loop is how coverage improves over time.

---

## 8. Implementation roadmap

| Phase | Goal | Key work | Exit criteria |
|---|---|---|---|
| **0 — PoC** (1–2 wks) | Prove retrieval is useful | Static 3-yr export → extract → KB on S3 Vectors → CLI/notebook Q&A; evaluate on hidden recent incidents | Hit-rate on held-out incidents clears an agreed bar (e.g., ≥60% useful leads) |
| **1 — MVP** (5–7 wks) | Live Slack-native assistant + shadow auto-post | Live ingestion, extraction w/ redaction, Bedrock Agent + KB, @-mention bot, trigger classifier + confidence gate **in shadow mode**, Guardrails, 👍/👎, kill switch | Stakeholders get cited, useful on-demand answers; shadow auto-post accuracy is being measured |
| **2 — Go live + harden** (3–5 wks) | Flip auto-post live; production-grade | Promote auto-post to the live channel behind the threshold; API Gateway + Events API; IAM least-privilege; Secrets Manager; full PII redaction; CloudWatch dashboards + cost alerts; optional VPC/PrivateLink | Passes security review; live auto-post within accuracy bar; SLOs defined |
| **3 — Evolve** (ongoing) | More value, more trust | Recency/quality re-ranking; "verified solution" curation; migrate orchestration to **AgentCore** (memory/policy/observability); **Datadog/ArgoCD link-outs**; pipe traces into Datadog | Measurable drop in time-to-first-useful-lead; sustained adoption |

For Phase 3: Amazon Bedrock AgentCore went generally available in October 2025 as a managed platform to build, deploy, and run agents at scale with session isolation, memory, and observability without managing infrastructure. Start with config-based Bedrock Agents for the MVP and graduate only when you need those production features.

---

*v2 incorporates: confirmed volume/residency/channel/autonomy/integration parameters; concrete cost estimate; S3 Vectors as the chosen store; the proactive auto-post branch with trigger classifier, confidence gate, and shadow-mode rollout.*
