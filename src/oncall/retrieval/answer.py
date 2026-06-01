#!/usr/bin/env python3
"""Read path A - ask a question and get a grounded, cited answer.

Embeds the question, retrieves the most similar past cases from the local index,
and asks Bedrock to answer using only those cases (with Slack permalinks as
citations). Pass --question for one-shot, or omit it for an interactive prompt.

Usage:
    export BEDROCK_MODEL_ID=...
    python -m oncall.retrieval.answer --index ./data/index.json \
        --question "argocd synced a bad manifest and pods are crashlooping"
"""
import argparse
import json
import os
import sys

from oncall.llm import bedrock_runtime, converse
from oncall.prompts import ANSWER_SYSTEM_PROMPT, build_answer_user_message
from oncall.retrieval.embeddings import embed_text
from oncall.retrieval.store import cosine_topk


def load_index(path):
    with open(path) as f:
        return json.load(f)["items"]


def answer(question, items, client, model_id, k, min_sim):
    if not question.strip():
        return "Empty question."
    q_vec = embed_text(client, question)
    hits = cosine_topk(q_vec, items, k=k)
    cases = []
    for item, sim in hits:
        if sim < min_sim:
            continue
        enriched = dict(item)
        enriched["similarity"] = sim
        enriched.pop("vector", None)
        cases.append(enriched)
    user_msg = build_answer_user_message(question, cases)
    return converse(client, model_id, ANSWER_SYSTEM_PROMPT, user_msg)


def main():
    p = argparse.ArgumentParser(description="Ask the on-call assistant.")
    p.add_argument("--index", default="./data/index.json")
    p.add_argument("--question", default=None, help="One-shot question; omit for interactive.")
    p.add_argument("--k", type=int, default=3, help="How many cases to retrieve.")
    p.add_argument("--min-sim", type=float, default=0.3,
                   help="Drop retrieved cases below this cosine similarity.")
    args = p.parse_args()

    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        sys.exit("Set BEDROCK_MODEL_ID to the model you enabled in Bedrock.")
    items = load_index(args.index)
    client = bedrock_runtime()

    if args.question:
        print(answer(args.question, items, client, model_id, args.k, args.min_sim))
        return
    print("On-call assistant. Ask a question (Ctrl-D to quit).")
    while True:
        try:
            q = input("\n> ")
        except EOFError:
            break
        print("\n" + answer(q, items, client, model_id, args.k, args.min_sim))


if __name__ == "__main__":
    main()
