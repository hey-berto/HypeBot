from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from hype_autopilot.phase2.config import load_phase2_config
from hype_autopilot.phase2.models import LLMStructuredOutput, LLMStructuredOutputV2
from hype_autopilot.phase2.provider import output_json_schema
from hype_autopilot.phase2.runner import GeometryViolation, validate_geometry

ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64


def no_trade() -> dict:
    return {
        "input_snapshot_hash": HASH,
        "output_schema_version": "LLM_OUTPUT_V2",
        "decision": "NO_TRADE",
        "confidence": "LOW",
        "rationale_tags": [],
        "bull_case": [],
        "bear_case": [],
        "data_conflicts": [],
        "invocation_reason": "SCHEDULED_RESEARCH",
        "entry": {"mode": "NONE", "trigger_price": None},
        "stop": None,
        "target": None,
        "invalidation": None,
        "ttl_minutes": None,
    }


def trade() -> dict:
    row = no_trade()
    row.update(
        decision="LONG",
        entry={"mode": "NOW", "trigger_price": None},
        stop={"kind": "ABSOLUTE_PRICE", "price": 95},
        target={"kind": "ABSOLUTE_PRICE", "price": 110.5},
        invalidation={"category": "TREND_BREAK", "reference_price": 95, "tags": []},
        ttl_minutes=60,
    )
    return row


def with_value(base: dict, path: str, value: object) -> dict:
    row = deepcopy(base)
    keys = path.split(".")
    node = row
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return row


def without(base: dict, path: str) -> dict:
    row = deepcopy(base)
    keys = path.split(".")
    node = row
    for key in keys[:-1]:
        node = node[key]
    del node[keys[-1]]
    return row


def test_every_transport_object_property_is_locally_required() -> None:
    schema = output_json_schema("LLM_OUTPUT_V2")
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(no_trade())
    assert Draft202012Validator(schema).is_valid(trade())
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    for name in ("PriceGeometryV2", "EntrySemanticsV2", "InvalidationV2"):
        nested = schema["$defs"][name]
        assert set(nested["required"]) == set(nested["properties"])
        assert nested["additionalProperties"] is False
    assert set(LLMStructuredOutputV2.model_fields) == set(schema["properties"])
    assert all(
        field.is_required() for field in LLMStructuredOutputV2.model_fields.values()
    )


@pytest.mark.parametrize(
    "field",
    [
        "rationale_tags",
        "bull_case",
        "bear_case",
        "data_conflicts",
        "invocation_reason",
        "stop",
        "target",
        "invalidation",
        "ttl_minutes",
    ],
)
def test_omitted_root_defaults_are_rejected_only_by_v2(field: str) -> None:
    row = without(no_trade(), field)
    LLMStructuredOutput.model_validate(row)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(row)


@pytest.mark.parametrize(
    ("base", "field"),
    [
        (trade, "stop.kind"),
        (trade, "target.kind"),
        (trade, "invalidation.reference_price"),
        (trade, "invalidation.tags"),
        (no_trade, "entry.trigger_price"),
    ],
)
def test_omitted_nested_defaults_are_rejected_by_v2(base, field: str) -> None:
    row = without(base(), field)
    LLMStructuredOutput.model_validate(row)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(row)


@pytest.mark.parametrize(
    ("path", "base"),
    [
        ("stop.price", trade),
        ("target.price", trade),
        ("invalidation.reference_price", trade),
        ("ttl_minutes", trade),
        ("entry.trigger_price", lambda: with_value(trade(), "entry.mode", "BREAKOUT")),
    ],
)
def test_numeric_strings_rejected_while_json_numbers_pass(path: str, base) -> None:
    row = base()
    number = 105 if path == "entry.trigger_price" else 95
    assert LLMStructuredOutputV2.model_validate(with_value(row, path, number))
    string_row = with_value(row, path, str(number))
    assert LLMStructuredOutput.model_validate(string_row)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(string_row)


@pytest.mark.parametrize("kind", ["absolute_stop", "absolute_target", "", "RELATIVE"])
def test_only_absolute_price_kind_is_accepted(kind: str) -> None:
    assert LLMStructuredOutputV2.model_validate(trade())
    for path in ("stop.kind", "target.kind"):
        with pytest.raises(ValidationError):
            LLMStructuredOutputV2.model_validate(with_value(trade(), path, kind))
    geometry = output_json_schema("LLM_OUTPUT_V2")["$defs"]["PriceGeometryV2"]
    assert geometry["properties"]["kind"]["const"] == "ABSOLUTE_PRICE"


@pytest.mark.parametrize(
    ("path", "base"),
    [
        ("stop.price", trade),
        ("target.price", trade),
        ("invalidation.reference_price", trade),
        ("entry.trigger_price", lambda: with_value(trade(), "entry.mode", "BREAKOUT")),
    ],
)
@pytest.mark.parametrize("bad", [0, -1])
def test_zero_and_negative_prices_are_rejected(path: str, base, bad: int) -> None:
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(with_value(base(), path, bad))
    schema = output_json_schema("LLM_OUTPUT_V2")
    if path.startswith(("stop.", "target.")):
        numeric = schema["$defs"]["PriceGeometryV2"]["properties"]["price"]
    elif path.startswith("invalidation."):
        numeric = schema["$defs"]["InvalidationV2"]["properties"]["reference_price"][
            "anyOf"
        ][0]
    else:
        numeric = schema["$defs"]["EntrySemanticsV2"]["properties"]["trigger_price"][
            "anyOf"
        ][0]
    assert numeric["exclusiveMinimum"] == 0


@pytest.mark.parametrize("category", ["", " ", "\t\n", "\u2003"])
def test_blank_invalidation_category_rejected(category: str) -> None:
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(
            with_value(trade(), "invalidation.category", category)
        )
    assert (
        output_json_schema("LLM_OUTPUT_V2")["$defs"]["InvalidationV2"]["properties"][
            "category"
        ]["pattern"]
        == r"\S"
    )


def test_valid_canonical_json_and_contextual_geometry() -> None:
    row = trade()
    output = LLMStructuredOutputV2.model_validate(row)
    snapshot = SimpleNamespace(
        market=SimpleNamespace(
            hype_context=SimpleNamespace(mark_price=100, mid_price=100)
        )
    )
    validate_geometry(output, snapshot)
    wrong_direction = LLMStructuredOutputV2.model_validate(
        with_value(row, "stop.price", 105)
    )
    with pytest.raises(GeometryViolation):
        validate_geometry(wrong_direction, snapshot)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(
            with_value(no_trade(), "stop", row["stop"])
        )
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(with_value(row, "entry.mode", "NONE"))
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(with_value(row, "entry.mode", "BREAKOUT"))


def test_epoch003_is_draft_only_and_research_settings_match_epoch002() -> None:
    old, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_002.yaml")
    new, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_003.yaml")
    assert new.phase2_epoch_id == "phase2_epoch_003"
    assert new.database_path == "data/phase2/phase2_epoch_003.sqlite3"
    assert not new.evidence_collection_enabled and not new.activation_authorized
    assert not (ROOT / new.database_path).exists()
    old_fields = old.model_dump()
    new_fields = new.model_dump()
    for key in ("phase2_epoch_id", "status", "database_path"):
        old_fields.pop(key)
        new_fields.pop(key)
    assert old_fields == new_fields


def test_epoch004_is_inactive_and_matches_epoch003_except_identity() -> None:
    old, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_003.yaml")
    new, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_004.yaml")
    assert new.phase2_epoch_id == "phase2_epoch_004"
    assert new.database_path == "data/phase2/phase2_epoch_004.sqlite3"
    new.assert_build_only()
    old_values = old.model_dump()
    new_values = new.model_dump()
    for key in ("phase2_epoch_id", "database_path"):
        old_values.pop(key)
        new_values.pop(key)
    assert new_values == old_values


def test_epoch005_is_inactive_and_matches_epoch004_except_identity() -> None:
    old, _ = load_phase2_config(ROOT / "config/phase2/phase2_epoch_004.yaml")
    new, digest = load_phase2_config(ROOT / "config/phase2/phase2_epoch_005.yaml")
    assert new.phase2_epoch_id == "phase2_epoch_005"
    assert new.database_path == "data/phase2/phase2_epoch_005.sqlite3"
    assert digest == "2118bb72f73495a190eb7550408c260de44da84153868da96f94f886b7030b12"
    new.assert_build_only()
    old_values = old.model_dump()
    new_values = new.model_dump()
    for key in ("phase2_epoch_id", "database_path"):
        old_values.pop(key)
        new_values.pop(key)
    assert new_values == old_values


@pytest.mark.parametrize(
    ("base", "path", "value"),
    [
        (trade, "stop.kind", "absolute_stop"),
        (trade, "target.kind", "absolute_target"),
        (trade, "stop.price", 0),
        (trade, "target.price", -1),
        (trade, "invalidation.reference_price", 0),
        (trade, "invalidation.category", "   "),
        (trade, "stop.price", "95"),
        (trade, "ttl_minutes", "60"),
        (
            lambda: with_value(trade(), "entry.mode", "BREAKOUT"),
            "entry.trigger_price",
            0,
        ),
    ],
)
def test_transport_and_local_both_reject_statically_invalid_values(
    base, path, value
) -> None:
    row = with_value(base(), path, value)
    assert not Draft202012Validator(output_json_schema("LLM_OUTPUT_V2")).is_valid(row)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(row)


@pytest.mark.parametrize(
    ("base", "path"),
    [
        (no_trade, "rationale_tags"),
        (no_trade, "stop"),
        (no_trade, "entry.trigger_price"),
        (trade, "stop.kind"),
        (trade, "invalidation.tags"),
    ],
)
def test_transport_and_local_both_reject_omission(base, path) -> None:
    row = without(base(), path)
    assert not Draft202012Validator(output_json_schema("LLM_OUTPUT_V2")).is_valid(row)
    with pytest.raises(ValidationError):
        LLMStructuredOutputV2.model_validate(row)
