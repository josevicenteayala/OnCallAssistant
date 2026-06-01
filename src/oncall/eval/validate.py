#!/usr/bin/env python3
"""Step 4 - Validate extraction quality.

Generates a self-contained HTML report putting each original thread next to its
extracted JSON, sorted by confidence, plus summary stats (resolved %, confidence
buckets, category counts, redaction count). This is how you eyeball ~30 threads
and decide whether the data is rich enough and where to set the confidence cutoff
before back-filling all three years.

Usage:
    python validate.py --threads ./data/normalized_threads.jsonl \
                       --cases ./data/structured_cases.jsonl \
                       --out ./data/validation_report.html
"""
import argparse
import html
import json
from collections import Counter


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def thread_text(thread):
    return "\n".join(f"{m['author']}: {m['text']}" for m in thread["messages"])


def conf_bucket(c):
    if c >= 0.85:
        return "0.85-1.0 (strong)"
    if c >= 0.5:
        return "0.5-0.84 (usable)"
    if c >= 0.2:
        return "0.2-0.49 (weak)"
    return "0.0-0.19 (noise)"


def build_report(threads, cases, out):
    by_ts = {t["thread_ts"]: t for t in threads}
    cases = sorted(cases, key=lambda c: c.get("confidence", 0), reverse=True)

    total = len(cases)
    resolved = sum(1 for c in cases if c.get("is_resolved"))
    redacted = sum(1 for c in cases if c.get("redaction_applied"))
    buckets = Counter(conf_bucket(c.get("confidence", 0)) for c in cases)
    cats = Counter(c.get("category", "?") for c in cases)
    indexable = sum(1 for c in cases
                    if c.get("is_resolved") and c.get("confidence", 0) >= 0.4)

    rows = []
    for c in cases:
        t = by_ts.get(c.get("thread_ts"))
        original = html.escape(thread_text(t)) if t else "(thread not found)"
        extracted = html.escape(json.dumps(c, indent=2, ensure_ascii=False))
        conf = c.get("confidence", 0)
        flag = "ok" if (c.get("is_resolved") and conf >= 0.4) else "drop"
        rows.append(f"""
        <tr class="{flag}">
          <td class="meta">
            <div class="conf">{conf:.2f}</div>
            <div class="cat">{html.escape(str(c.get('category','?')))}</div>
            <div class="badge {flag}">{flag.upper()}</div>
          </td>
          <td><pre>{original}</pre></td>
          <td><pre>{extracted}</pre></td>
        </tr>""")

    def stat_rows(counter):
        return "".join(f"<li>{html.escape(k)}: <b>{v}</b></li>"
                       for k, v in counter.most_common())

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Extraction validation</title>
<style>
  body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a}}
  h1{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:20px}}
  .cards{{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:24px}}
  .card{{border:1px solid #e3e3e3;border-radius:10px;padding:14px 18px;min-width:200px}}
  .card h3{{margin:0 0 8px;font-size:13px;text-transform:uppercase;color:#888;letter-spacing:.04em}}
  .card ul{{margin:0;padding-left:18px}} .big{{font-size:28px;font-weight:700}}
  table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #e3e3e3;vertical-align:top;padding:10px;text-align:left}}
  th{{background:#fafafa;position:sticky;top:0}}
  pre{{white-space:pre-wrap;word-break:break-word;margin:0;font:12px/1.45 ui-monospace,Menlo,monospace}}
  .meta{{width:90px;text-align:center}} .conf{{font-size:20px;font-weight:700}}
  .cat{{font-size:11px;color:#666;margin:4px 0}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}}
  .badge.ok{{background:#e3f5e8;color:#1c7a3e}} .badge.drop{{background:#fde8e8;color:#b42323}}
  tr.drop{{background:#fffafa}}
</style></head><body>
<h1>Extraction validation report</h1>
<div class="sub">Sorted by confidence. "OK" = would be indexed (resolved &amp; confidence &ge; 0.4); "DROP" = held for review.</div>
<div class="cards">
  <div class="card"><h3>Cases</h3><div class="big">{total}</div></div>
  <div class="card"><h3>Resolved</h3><div class="big">{resolved}</div><div class="sub">{(resolved/total*100 if total else 0):.0f}% of cases</div></div>
  <div class="card"><h3>Would index</h3><div class="big">{indexable}</div><div class="sub">at cutoff 0.4</div></div>
  <div class="card"><h3>Redacted</h3><div class="big">{redacted}</div></div>
  <div class="card"><h3>Confidence</h3><ul>{stat_rows(buckets)}</ul></div>
  <div class="card"><h3>Categories</h3><ul>{stat_rows(cats)}</ul></div>
</div>
<table>
  <tr><th>Meta</th><th>Original thread</th><th>Extracted JSON</th></tr>
  {''.join(rows)}
</table></body></html>"""

    with open(out, "w") as f:
        f.write(page)
    print(f"Report -> {out}")
    print(f"  {total} cases | {resolved} resolved | {indexable} indexable at cutoff 0.4")
    print("  Open the HTML, scan top-to-bottom, and check: are the OK rows truly")
    print("  good fixes, and are any DROP rows actually useful (cutoff too high)?")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Validate extraction quality.")
    p.add_argument("--threads", default="./data/normalized_threads.jsonl")
    p.add_argument("--cases", default="./data/structured_cases.jsonl")
    p.add_argument("--out", default="./data/validation_report.html")
    args = p.parse_args()
    build_report(load_jsonl(args.threads), load_jsonl(args.cases), args.out)
