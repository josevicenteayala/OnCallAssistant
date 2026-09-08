# Docs index

| Document | What it's for | Kind |
|---|---|---|
| [`design-v2.md`](design-v2.md) | The full design: architecture, technology choices, cost, risks, phased roadmap. **Starts with an as-built status table** showing what's deployed vs. still designed-only. | Design intent |
| [`data-pipeline.md`](data-pipeline.md) | How to run the backfill end to end (export → normalize → extract → validate → upload → holdout), and how to read both reports. | Runbook |
| [`extraction-prompt.md`](extraction-prompt.md) | Annotated spec for the extraction prompt with worked examples. The executable copy lives in [`../src/oncall/prompts.py`](../src/oncall/prompts.py). | Spec |
| [`../src/oncall/lambdas/README.md`](../src/oncall/lambdas/README.md) | Live-track ops runbook: env vars, IAM, timeouts, deployment, troubleshooting table. | Runbook |
| [`oncall-assistant-poc.html`](oncall-assistant-poc.html) | Six-slide stakeholder walkthrough. Open in a browser. | Deck |
| [`OnCallAssistant-Infrastructure-Guide.pdf`](OnCallAssistant-Infrastructure-Guide.pdf) | **Client-facing**: everything a customer platform team needs to recreate the system in their own AWS account and Slack workspace — resource inventory, IAM, Slack app setup, deployment order, smoke test, troubleshooting. Regenerable source in [`infrastructure-guide/`](infrastructure-guide/); read its README before sending to a client. | Deliverable |

## Diagrams

All hand-authored SVG — edit the source, no build step. Validate with the bounds
check in the repo history, or just open them in a browser.

| Diagram | Shows |
|---|---|
| [`diagrams/architecture.svg`](diagrams/architecture.svg) | Whole system: Slack → API GW → two Lambdas → S3 (`events/` audit + `cases/` indexed) → Bedrock KB → Titan → S3 Vectors → Nova. |
| [`diagrams/flow-a-ingestion.svg`](diagrams/flow-a-ingestion.svg) | Write path as a sequence, including the resolution-only band: extract → redact → confidence gate → `cases/` → sync. |
| [`diagrams/flow-b-query.svg`](diagrams/flow-b-query.svg) | Read path as a sequence: @mention → optional thread context → retrieve_and_generate → citation rebuild → in-thread reply. |
| [`diagrams/components.svg`](diagrams/components.svg) | Layered component view: channels, compute, storage, intelligence — with each component's responsibilities. |
| [`diagrams/slack-mockup.svg`](diagrams/slack-mockup.svg) | What the bot's reply looks like in Slack. |

## Keeping these honest

These documents describe a system that is deployed and changing. Two rules keep
them from rotting:

1. **`prompts.py` and the Lambda code are the source of truth.** When docs and
   code disagree, the code wins and the doc is the bug.
2. **The confidence cutoff appears in four places** — `make index`, `make upload`,
   the live Lambda's `CONFIDENCE_CUTOFF`, and the prose here. Change one, change
   all four.
