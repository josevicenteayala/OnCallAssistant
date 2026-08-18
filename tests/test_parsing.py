"""Shared model-output parsing tests — pure local.

Guards the contract flagged in PR #1 review: parse_case returns a dict or
None, never another JSON type, so callers can use dict operations safely.
"""
from oncall.extract.parsing import parse_case, strip_fences


class TestParseCaseReturnsDictOrNone:
    def test_plain_object(self):
        assert parse_case('{"a": 1}') == {"a": 1}

    def test_fenced_object(self):
        assert parse_case('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_list_is_none(self):
        assert parse_case("[1, 2, 3]") is None

    def test_json_string_is_none(self):
        assert parse_case('"just a string"') is None

    def test_list_containing_hit_is_none(self):
        # The exact holdout-judge hazard: '"hit" in ["hit"]' is True but
        # verdict["hit"] would raise TypeError if this ever returned a list.
        assert parse_case('["hit"]') is None

    def test_number_is_none(self):
        assert parse_case("42") is None

    def test_object_inside_prose_is_extracted(self):
        assert parse_case('Sure! Here it is: {"a": 1} hope that helps') == {"a": 1}

    def test_object_inside_list_is_extracted_by_brace_fallback(self):
        assert parse_case('[{"a": 1}]') == {"a": 1}

    def test_garbage_is_none(self):
        assert parse_case("not json at all") is None


class TestStripFences:
    def test_strips_json_fence(self):
        assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_leaves_plain_text(self):
        assert strip_fences('{"a": 1}') == '{"a": 1}'
