#!/usr/bin/env python3
"""Read path A - build a local vector index from the structured cases.

Embeds each indexable case (resolved and confidence >= cutoff) with Titan and
writes a single JSON index file. This is the laptop-friendly stand-in for the
managed Bedrock Knowledge Base; the infra step replaces it without touching the
answer prompt.

Usage:
    python -m oncall.retrieval.index --cases ./data/structured_cases.jsonl \
                                     --out ./data/index.json --cutoff 0.4
"""
import argparse
import json

from oncall.retrieval.embeddings import embed_client, embed_text

# Fields carried into the index for use by the answer prompt and citations.
KEEP = ["permalink", "summary", "issue", "root_cause", "solution",
        "affected_service", "category", "confidence"]


def retrieval_text(case: dict) -> str:
    """The string we embed: summary first, falling back to issue."""
    return " ".join(x for x in (case.get("summary"), case.get("issue")) if x).strip()


def build(cases_path, out_path, cutoff):
    client = embed_client()
    items, skipped = [], 0
    with open(cases_path) as f:
        for line in f:
            c = json.loads(line)
            if not (c.get("is_resolved") and c.get("confidence", 0) >= cutoff):
                skipped += 1
                continue
            text = retrieval_text(c)
            if not text:
                skipped += 1
                continue
            item = {k: c.get(k) for k in KEEP}
            item["vector"] = embed_text(client, text)
            items.append(item)

    with open(out_path, "w") as f:
        json.dump({"cutoff": cutoff, "items": items}, f)
    print(f"Indexed {len(items)} cases (skipped {skipped}) -> {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build a local vector index.")
    p.add_argument("--cases", default="./data/structured_cases.jsonl")
    p.add_argument("--out", default="./data/index.json")
    p.add_argument("--cutoff", type=float, default=0.4,
                   help="Min confidence to index (tune from the validation report).")
    args = p.parse_args()
    build(args.cases, args.out, args.cutoff)
