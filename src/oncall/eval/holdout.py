"""Held-out retrieval evaluation — the PoC's go/no-go metric (design §5).

Takes the N most recent indexable cases, removes them from the index, asks the
retriever about each one's *issue* text (the symptom, as an engineer would
report it — never the summary, which contains the fix), and has an LLM judge
grade whether any retrieved lead points at the actual resolution. Writes an
HTML report for human review and prints the hit-rate.

The exit bar from design-v2.md §8: >= 60% useful leads on held-out incidents.

Usage (index.json must already exist — run `make index` first):
    python -m oncall.eval.holdout --cases ./data/structured_cases.jsonl \
        --index ./data/index.json --n 25 --out ./data/holdout_report.html
"""
import argparse
import html
import json
import os
import sys

from oncall.extract.parsing import parse_case
from oncall.llm import bedrock_runtime, converse
from oncall.prompts import HOLDOUT_JUDGE_SYSTEM_PROMPT, build_judge_user_message
from oncall.retrieval.embeddings import embed_text
from oncall.retrieval.store import cosine_topk

EXIT_BAR = 0.60


def select_holdout(cases: list[dict], n: int, cutoff: float = 0.4) -> list[dict]:
    """The N most recent cases that would have been indexed and have an issue text."""
    eligible = [
        c for c in cases
        if c.get("is_resolved") and c.get("confidence", 0) >= cutoff and c.get("issue")
    ]
    eligible.sort(key=lambda c: float(c.get("thread_ts") or 0), reverse=True)
    return eligible[:n]


def split_index(items: list[dict], holdout: list[dict]) -> list[dict]:
    """Index items minus the held-out cases (matched by permalink)."""
    held = {c.get("permalink") for c in holdout}
    return [it for it in items if it.get("permalink") not in held]


def retrieve_leads(client, question: str, items: list[dict], k: int, min_sim: float) -> list[dict]:
    q_vec = embed_text(client, question)
    leads = []
    for item, sim in cosine_topk(q_vec, items, k=k):
        if sim < min_sim:
            continue
        lead = {key: item.get(key) for key in
                ("issue", "root_cause", "solution", "affected_service", "permalink")}
        lead["similarity"] = sim
        leads.append(lead)
    return leads


def judge(client, model_id: str, case: dict, leads: list[dict]) -> dict:
    """Return {"hit": bool, "reason": str} for one held-out incident."""
    if not leads:
        return {"hit": False, "reason": "No leads retrieved above the similarity floor."}
    user_msg = build_judge_user_message(
        case["issue"], case.get("root_cause"), case.get("solution"), leads
    )
    raw = converse(client, model_id, HOLDOUT_JUDGE_SYSTEM_PROMPT, user_msg, max_tokens=300)
    verdict = parse_case(raw)
    if not verdict or "hit" not in verdict:
        return {"hit": False, "reason": f"Judge output did not parse: {raw[:200]}"}
    return {"hit": bool(verdict["hit"]), "reason": str(verdict.get("reason", ""))}


def hit_rate(results: list[dict]) -> float:
    return sum(1 for r in results if r["hit"]) / len(results) if results else 0.0


def build_report(results: list[dict], out: str, k: int, min_sim: float) -> None:
    rate = hit_rate(results)
    verdict_word = "PASS" if rate >= EXIT_BAR else "FAIL"
    rows = []
    for r in results:
        leads_html = "<br>".join(
            f"[{i}] sim={lead['similarity']:.2f} {html.escape(str(lead.get('issue')))}"
            f" → {html.escape(str(lead.get('solution')))}"
            for i, lead in enumerate(r["leads"], 1)
        ) or "(none)"
        flag = "hit" if r["hit"] else "miss"
        rows.append(f"""
        <tr class="{flag}">
          <td><span class="badge {flag}">{flag.upper()}</span></td>
          <td><pre>{html.escape(r['case']['issue'])}</pre></td>
          <td><pre>root cause: {html.escape(str(r['case'].get('root_cause')))}
solution: {html.escape(str(r['case'].get('solution')))}</pre></td>
          <td>{leads_html}</td>
          <td>{html.escape(r['reason'])}</td>
        </tr>""")

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Held-out retrieval evaluation</title>
<style>
  body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a}}
  h1{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:20px}}
  .big{{font-size:34px;font-weight:700}}
  .pass{{color:#1c7a3e}} .fail{{color:#b42323}}
  table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #e3e3e3;vertical-align:top;padding:10px;text-align:left}}
  th{{background:#fafafa;position:sticky;top:0}}
  pre{{white-space:pre-wrap;word-break:break-word;margin:0;font:12px/1.45 ui-monospace,Menlo,monospace}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}}
  .badge.hit{{background:#e3f5e8;color:#1c7a3e}} .badge.miss{{background:#fde8e8;color:#b42323}}
</style></head><body>
<h1>Held-out retrieval evaluation</h1>
<div class="sub">{len(results)} held-out incidents · top-{k} retrieval · min similarity {min_sim}
· exit bar {EXIT_BAR:.0%} (design-v2.md §8)</div>
<div class="big {verdict_word.lower()}">{rate:.0%} hit-rate — {verdict_word}</div>
<p class="sub">The LLM judge's verdicts are a first pass — scan the MISS rows (and a few HITs)
yourself before trusting the number.</p>
<table>
  <tr><th>Verdict</th><th>Incident (as asked)</th><th>Actual resolution (hidden)</th>
      <th>Retrieved leads</th><th>Judge's reason</th></tr>
  {''.join(rows)}
</table></body></html>"""
    with open(out, "w") as f:
        f.write(page)


def run(cases_path, index_path, n, k, min_sim, out):
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        sys.exit("Set BEDROCK_MODEL_ID to the model you enabled in Bedrock.")

    with open(cases_path) as f:
        cases = [json.loads(line) for line in f]
    with open(index_path) as f:
        index = json.load(f)

    holdout = select_holdout(cases, n, cutoff=index.get("cutoff", 0.4))
    if not holdout:
        sys.exit("No indexable cases to hold out — run the pipeline first.")
    remaining = split_index(index["items"], holdout)
    print(f"Held out {len(holdout)} most recent cases; retrieving against "
          f"{len(remaining)} remaining.")

    client = bedrock_runtime()
    results = []
    for i, case in enumerate(holdout, 1):
        leads = retrieve_leads(client, case["issue"], remaining, k, min_sim)
        verdict = judge(client, model_id, case, leads)
        results.append({"case": case, "leads": leads, **verdict})
        print(f"  [{i}/{len(holdout)}] {'HIT ' if verdict['hit'] else 'miss'} — "
              f"{case['issue'][:70]}")

    build_report(results, out, k, min_sim)
    rate = hit_rate(results)
    print(f"\nHit-rate: {rate:.0%} on {len(results)} held-out incidents "
          f"(exit bar {EXIT_BAR:.0%}: {'PASS' if rate >= EXIT_BAR else 'FAIL'})")
    print(f"Report -> {out}  (review the misses before trusting the number)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Held-out retrieval evaluation (go/no-go).")
    p.add_argument("--cases", default="./data/structured_cases.jsonl")
    p.add_argument("--index", default="./data/index.json")
    p.add_argument("--n", type=int, default=25, help="How many recent cases to hold out.")
    p.add_argument("--k", type=int, default=3, help="Leads to retrieve per incident.")
    p.add_argument("--min-sim", type=float, default=0.3)
    p.add_argument("--out", default="./data/holdout_report.html")
    args = p.parse_args()
    run(args.cases, args.index, args.n, args.k, args.min_sim, args.out)
