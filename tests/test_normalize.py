"""Tests for the normalizer's text cleaning — pure local, no network needed.

These run today (before any Slack/Bedrock access) and prove the build/test loop
works end to end.
"""
from oncall.ingest.normalize import clean_text

USERS = {"U123": "alice", "U999": "bob"}


def test_resolves_mentions():
    assert clean_text("hey <@U123> can you look?", USERS) == "hey @alice can you look?"


def test_unknown_mention_falls_back_to_id():
    assert clean_text("ping <@U000>", USERS) == "ping @U000"


def test_unwraps_links_with_label():
    assert clean_text("see <https://x.io/d|the dashboard>", USERS) == "see the dashboard"


def test_unwraps_bare_links():
    assert clean_text("logs at <https://x.io/logs>", USERS) == "logs at https://x.io/logs"


def test_decodes_entities_and_channels():
    assert clean_text("a &amp; b in <#C1|alerts>", USERS) == "a & b in #alerts"


def test_empty_is_empty():
    assert clean_text("", USERS) == ""
