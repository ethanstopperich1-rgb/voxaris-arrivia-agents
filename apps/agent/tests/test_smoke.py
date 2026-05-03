"""Smoke tests — module imports and persona invariants.

Real conversation tests live in test_state.py once Phase 1B lands.
"""

from __future__ import annotations


def test_module_imports() -> None:
    from voxaris_agent import worker

    assert worker.entrypoint is not None
    assert worker.cli_main is not None


def test_persona_forbids_human_claim() -> None:
    """Hard rule: agent must answer truthfully when asked if it's AI."""
    from voxaris_agent.worker import render_persona

    text = render_persona().lower()
    assert "ai" in text
    assert "never claim to be human" in text
    assert "yes, i'm deedy, an ai assistant" in text


def test_persona_lists_six_gates() -> None:
    from voxaris_agent.worker import render_persona

    text = render_persona().lower()
    for gate in (
        "age",
        "income",
        "decision-makers",
        "credit card",
        "twenty-four months",
        "residency",
    ):
        assert gate in text, f"missing qualification gate: {gate}"


def test_greeting_under_15_seconds_budget() -> None:
    from voxaris_agent.worker import render_greeting

    assert "fifteen seconds" in render_greeting().lower()


def test_persona_substitutes_resort_and_incentive() -> None:
    """Per-call metadata must override the defaults inside the rendered
    prompt — no literal '{resort_name}' should ever reach the model."""
    from voxaris_agent.worker import render_persona, render_greeting

    persona = render_persona({"resort_name": "Westgate Resorts"})
    greeting = render_greeting(
        {"resort_name": "Westgate Resorts", "incentive": "test getaway"}
    )

    assert "{resort_name}" not in persona
    assert "{incentive}" not in greeting
    assert "Westgate Resorts" in persona
    assert "Westgate Resorts" in greeting
    assert "test getaway" in greeting


def test_persona_includes_deposit_branching() -> None:
    """Both stay-type branches must appear so Grok can pick the right one."""
    from voxaris_agent.worker import render_persona

    text = render_persona().lower()
    assert "on_property" in text
    assert "off_property" in text
    assert "$75 hold" in render_persona()
    # Words may be split across lines — collapse whitespace before searching.
    flat = " ".join(render_persona().split()).lower()
    assert "refundable credit card deposit" in flat


def test_persona_names_agent_deedy() -> None:
    from voxaris_agent.worker import render_persona, render_greeting

    assert "Deedy" in render_persona()
    assert "Deedy" in render_greeting()


def test_metadata_parser_handles_garbage() -> None:
    from voxaris_agent.worker import parse_metadata

    assert parse_metadata(None) == {}
    assert parse_metadata("") == {}
    assert parse_metadata("not json at all") == {}
    assert parse_metadata('{"resort_name": "Westgate"}') == {"resort_name": "Westgate"}
    # None values dropped, others stringified
    assert parse_metadata('{"a": null, "b": 7}') == {"b": "7"}
