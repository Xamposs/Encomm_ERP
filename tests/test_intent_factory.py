"""Tests for core/intent_factory.py."""
import pytest

from core.intent_factory import IntentFactory, VALID_INTENTS


@pytest.fixture()
def factory():
    return IntentFactory()


def test_valid_intent_passes(factory):
    result = factory.parse({"intent": "view_dashboard", "parameters": {}})
    assert result["intent"] == "view_dashboard"
    assert result["parameters"] == {}


def test_parameters_default_to_empty_dict(factory):
    result = factory.parse({"intent": "search_inventory"})
    assert result["parameters"] == {}


def test_unknown_intent_falls_back_safely(factory):
    result = factory.parse({"intent": "DROP_TABLE", "parameters": {}})
    assert result["intent"] == "unknown"
    assert "reason" in result["parameters"]


def test_none_input_returns_unknown(factory):
    result = factory.parse(None)
    assert result["intent"] == "unknown"


def test_non_dict_parameters_replaced(factory):
    result = factory.parse({"intent": "search_inventory", "parameters": "evil"})
    assert result["intent"] == "search_inventory"
    assert result["parameters"] == {}


def test_intent_strips_and_lowercases(factory):
    result = factory.parse({"intent": "  View_POS  ", "parameters": {}})
    assert result["intent"] == "view_pos"


def test_parse_json_string_valid(factory):
    result = factory.parse_json_string('{"intent": "view_pos", "parameters": {}}')
    assert result["intent"] == "view_pos"


def test_parse_json_string_malformed(factory):
    result = factory.parse_json_string("not json at all")
    assert result["intent"] == "unknown"


def test_intent_non_string_value_does_not_crash(factory):
    """An LLM may return intent: null or a list — must not raise."""
    result = factory.parse({"intent": None, "parameters": {}})
    assert result["intent"] == "unknown"
    result2 = factory.parse({"intent": ["view_pos"], "parameters": {}})
    assert result2["intent"] == "unknown"
