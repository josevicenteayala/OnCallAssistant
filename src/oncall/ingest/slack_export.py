#!/usr/bin/env python3
"""Step 1 - Export Slack channel history.

Pulls every top-level message (and, for threaded ones, the full reply chain)
from a single channel over a time window, plus a user-id -> name map and a
permalink per thread. Writes:

    <outdir>/raw_threads.jsonl   one JSON object per thread
    <outdir>/users.json          {user_id: display_name}

This script only READS from Slack. It needs a bot token with scopes:
    channels:history, channels:read, users:read
(add groups:history / groups:read if the channel is private).

The token is read from the SLACK_BOT_TOKEN environment variable. Never hardcode
it and never commit it - store it in a secret manager for anything beyond a
local PoC run.

Usage:
    export SLACK_BOT_TOKEN=xoxb-...
    python slack_export.py --channel C0XXXXXXX --years 3 --outdir ./data
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

# Slack message subtypes that are channel noise, not incidents.
SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic",
    "channel_purpose", "channel_name", "channel_archive", "channel_unarchive",
}


def _call(fn, **kwargs):
    """Call a Slack API method, retrying politely on rate limits."""
    while True:
        try:
            return fn(**kwargs)
        except SlackApiError as e:
            if e.response.status_code == 429:
                wait = int(e.response.headers.get("Retry-After", "5"))
                print(f"  rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait + 1)
                continue
            raise


def fetch_user_map(client) -> dict:
    """Build {user_id: display_name} so normalization can resolve @mentions."""
    users, cursor = {}, None
    while True:
        resp = _call(client.users_list, cursor=cursor, limit=200)
        for m in resp["members"]:
            profile = m.get("profile", {})
            name = (profile.get("display_name")
                    or profile.get("real_name")
                    or m.get("name")
                    or m["id"])
            users[m["id"]] = name
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return users


def fetch_replies(client, channel, thread_ts):
    """Return the full ordered message list for one thread."""
    messages, cursor = [], None
    while True:
        resp = _call(client.conversations_replies,
                     channel=channel, ts=thread_ts, cursor=cursor, limit=200)
        messages.extend(resp["messages"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not resp.get("has_more"):
            break
    return messages


def permalink_for(client, channel, ts):
    try:
        return _call(client.chat_getPermalink,
                     channel=channel, message_ts=ts)["permalink"]
    except SlackApiError:
        return f"slack://channel/{channel}/{ts}"


def export(channel, years, outdir):
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        sys.exit("Set SLACK_BOT_TOKEN in your environment first.")
    os.makedirs(outdir, exist_ok=True)
    client = WebClient(token=token)

    print("Fetching user map...")
    users = fetch_user_map(client)
    with open(os.path.join(outdir, "users.json"), "w") as f:
        json.dump(users, f, indent=2)
    print(f"  {len(users)} users")

    oldest = (datetime.now(timezone.utc) - timedelta(days=365 * years)).timestamp()

    print(f"Exporting channel {channel} (last {years} years)...")
    threads_written = 0
    cursor = None
    out_path = os.path.join(outdir, "raw_threads.jsonl")
    with open(out_path, "w") as out:
        while True:
            resp = _call(client.conversations_history,
                         channel=channel, oldest=str(oldest),
                         cursor=cursor, limit=200)
            for msg in resp["messages"]:
                if msg.get("subtype") in SKIP_SUBTYPES:
                    continue
                ts = msg["ts"]
                # Threaded parent -> pull the whole thread; else single message.
                if msg.get("reply_count", 0) > 0:
                    messages = fetch_replies(client, channel, ts)
                else:
                    messages = [msg]
                record = {
                    "thread_ts": ts,
                    "channel": channel,
                    "permalink": permalink_for(client, channel, ts),
                    "messages": messages,
                }
                out.write(json.dumps(record) + "\n")
                threads_written += 1
                if threads_written % 50 == 0:
                    print(f"  {threads_written} threads...")
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not resp.get("has_more"):
                break

    print(f"Done. {threads_written} threads -> {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Export a Slack channel's threads.")
    p.add_argument("--channel", required=True, help="Channel ID, e.g. C0XXXXXXX")
    p.add_argument("--years", type=int, default=3, help="How far back to export")
    p.add_argument("--outdir", default="./data", help="Output directory")
    args = p.parse_args()
    export(args.channel, args.years, args.outdir)
