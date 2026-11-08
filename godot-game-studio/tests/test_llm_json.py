import pytest

from core.llm_client import extract_json_text


def test_extract_from_fence():
    assert extract_json_text('text ```json\n{"a": 1}\n``` more') == '{"a": 1}'


def test_extract_from_plain_prose():
    assert extract_json_text('here: {"a": {"b": 2}} done') == '{"a": {"b": 2}}'


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json_text("no braces here")
