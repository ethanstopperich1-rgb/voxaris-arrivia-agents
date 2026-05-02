"""Smoke tests — module imports and persona invariants.

Real conversation tests live in test_state.py once Phase 1B lands.
"""

from __future__ import annotations


def test_module_imports() -> None:
    from voxaris_agent import worker

    assert worker.entrypoint is not None
    assert worker.cli_main is not None


def test_persona_forbids_human_claim() -> None:
    """Sanity-check the system prompt — Hard Rule #1 from the build plan
    is that the agent must answer truthfully when asked if it's AI."""
    from voxaris_agent.worker import PERSONA_INSTRUCTIONS

    text = PERSONA_INSTRUCTIONS.lower()
    assert "ai" in text
    assert "never claim to be human" in text
    assert "yes, i'm an ai assistant" in text


def test_persona_lists_six_gates() -> None:
    from voxaris_agent.worker import PERSONA_INSTRUCTIONS

    text = PERSONA_INSTRUCTIONS.lower()
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
    """The greeting instruction explicitly caps at 15s — cheap regression
    guard so a future edit doesn't let it balloon."""
    from voxaris_agent.worker import GREETING_INSTRUCTIONS

    assert "fifteen seconds" in GREETING_INSTRUCTIONS.lower()
