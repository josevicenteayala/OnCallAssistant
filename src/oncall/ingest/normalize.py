#!/usr/bin/env python3
"""Step 2 - Normalize raw Slack threads.

Turns the raw export into the clean shape the extraction prompt expects:

    {"permalink": "...", "thread_ts": "...",
     "messages": [{"author": "alice", "ts": "...", "text": "..."}]}

It resolves <@U123> mentions to handles, unwraps Slack link markup
(<http://x|label> -> label), decodes HTML entities, and drops empty/system
messages. No network calls - pure local transform.

Usage:
    python normalize.py --indir ./data --outfile ./data/normalized_threads.jsonl
"""
import argparse
import html
import json
import os
import re
import sys

MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
CHANNEL_RE = re.compile(r"<#[A-Z0-9]+\|([^>]+)>")
SPECIAL_RE = re.compile(r"<!([^>]+)>")  # <!here>, <!channel>, etc.


def clean_text(text, users):
    if not text:
        return ""
    text = MENTION_RE.sub(lambda m: "@" + users.get(m.group(1), m.group(1)), text)
    text = LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = CHANNEL_RE.sub(lambda m: "#" + m.group(1), text)
    text = SPECIAL_RE.sub(lambda m: "@" + m.group(1).split("|")[0], text)
    return html.unescape(text).strip()


def author_of(msg, users):
    if msg.get("user"):
        return users.get(msg["user"], msg["user"])
    # Bot/integration messages (PagerDuty, Datadog, etc.) - keep them, they
    # often contain the alert itself.
    return msg.get("username") or (f"bot:{msg['bot_id']}" if msg.get("bot_id") else "unknown")


def normalize(indir, outfile):
    raw_path = os.path.join(indir, "raw_threads.jsonl")
    users_path = os.path.join(indir, "users.json")
    if not os.path.exists(raw_path):
        sys.exit(f"Not found: {raw_path} (run slack_export.py first)")
    with open(users_path) as f:
        users = json.load(f)

    kept = 0
    with open(raw_path) as src, open(outfile, "w") as dst:
        for line in src:
            thread = json.loads(line)
            messages = []
            for msg in thread["messages"]:
                text = clean_text(msg.get("text", ""), users)
                if not text:
                    continue
                messages.append({
                    "author": author_of(msg, users),
                    "ts": msg["ts"],
                    "text": text,
                })
            if not messages:
                continue
            dst.write(json.dumps({
                "thread_ts": thread["thread_ts"],
                "permalink": thread["permalink"],
                "messages": messages,
            }) + "\n")
            kept += 1
    print(f"Normalized {kept} threads -> {outfile}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Normalize raw Slack threads.")
    p.add_argument("--indir", default="./data", help="Dir with raw_threads.jsonl + users.json")
    p.add_argument("--outfile", default="./data/normalized_threads.jsonl")
    args = p.parse_args()
    normalize(args.indir, args.outfile)
