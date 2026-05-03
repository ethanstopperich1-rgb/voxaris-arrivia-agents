"""Smoke tests — module imports and persona invariants.

The persona is built from the canonical OPC qualification source-of-truth:
- docs/source/OPC_Qualification_Guide.md
- docs/source/VBA_Brief_Final.md
- docs/source/VBA_Pitch_Deck.md
"""

from __future__ import annotations


def test_module_imports() -> None:
    from voxaris_agent import worker

    assert worker.entrypoint is not None
    assert worker.cli_main is not None


def _flat(text: str) -> str:
    """Collapse whitespace + lowercase so newline-split phrases still match."""
    return " ".join(text.split()).lower()


def test_persona_forbids_human_claim() -> None:
    """Hard rule: agent must answer truthfully when asked if it's AI."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "ai" in text
    assert "never claim to be human" in text
    assert "yes, i'm deedy, an ai assistant for" in text


def test_persona_lists_all_nine_canonical_gates() -> None:
    """Per OPC_Qualification_Guide.md — there are nine gates, not six."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    for gate in (
        "twenty-five or older",       # 1 age
        "fifty thousand",             # 2 income
        "decision-makers",            # 3 decision makers
        "credit card",                # 4 creditworthiness
        "active bankruptcy",          # 4 bankruptcy clause
        "employed, self-employed",    # 5 employment
        "six to twelve months",       # 6 tour history (NOT 24)
        "outside the local",          # 7 residency
        "english",                    # 8 language
        "ninety to one hundred",      # 9 attendance commitment
    ):
        assert gate in text, f"missing canonical gate: {gate}"


def test_persona_uses_five_stage_flow_not_checklist() -> None:
    """Per Brief + Pitch Deck slide 5: Hook / Rapport / Soft / Hard / Close."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    for stage in ("hook", "rapport", "soft qualification", "hard qualification", "confirmation & close"):
        assert stage in text, f"missing flow stage: {stage}"


def test_white_label_greeting_names_resort_only() -> None:
    """Per Brief: white-labeled, invisible to the timeshare partner.
    The caller-facing greeting must not mention Voxaris or Arrivia.
    (The persona's rule line *names* them in order to forbid them —
    that's fine, the model never speaks system rules out loud.)"""
    from voxaris_agent.worker import render_greeting

    rendered = render_greeting({"resort_name": "Westgate Resorts"})
    assert "Voxaris" not in rendered, "Voxaris must not appear in greeting"
    assert "Arrivia" not in rendered, "Arrivia must not appear in greeting"
    assert "Westgate Resorts" in rendered


def test_persona_explicitly_forbids_voxaris_arrivia_to_caller() -> None:
    """The system prompt must contain the white-label rule, otherwise
    the model has no reason to suppress those brand names."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "do not" in text and "voxaris" in text and "arrivia" in text
    assert "white-label" in text or "white labeled" in text or "white-labeled" in text


def test_greeting_under_15_seconds_budget() -> None:
    from voxaris_agent.worker import render_greeting

    assert "fifteen seconds" in _flat(render_greeting())


def test_persona_substitutes_resort_and_incentive() -> None:
    from voxaris_agent.worker import render_greeting, render_persona

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
    """Per OPC Guide footer + Pitch Deck slide 4: on-property = folio hold,
    off-property = CC deposit."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "on_property" in text
    assert "off_property" in text
    assert "seventy-five dollar hold on your resort folio" in text
    assert "refundable credit-card deposit" in text


def test_persona_names_agent_deedy() -> None:
    from voxaris_agent.worker import render_greeting, render_persona

    assert "Deedy" in render_persona()
    assert "Deedy" in render_greeting()


def test_persona_never_leads_with_word_timeshare() -> None:
    """Per Brief: 'Timeshare is not a sought-after product, it is sold.'
    Lead with the experience and the incentive."""
    from voxaris_agent.worker import render_persona

    text = render_persona()
    # "timeshare" can appear as a domain term in the prompt's *guidance*,
    # but the explicit rule "never lead with the word 'timeshare'" must
    # be present.
    assert "never lead with the word" in _flat(text)


def test_persona_lists_four_tools() -> None:
    from voxaris_agent.worker import render_persona

    text = render_persona()
    assert "lookup_objection" in text
    assert "record_answer" in text
    assert "transfer_to_human" in text
    assert "detect_voicemail" in text


def test_metadata_parser_handles_garbage() -> None:
    from voxaris_agent.worker import parse_metadata

    assert parse_metadata(None) == {}
    assert parse_metadata("") == {}
    assert parse_metadata("not json at all") == {}
    assert parse_metadata('{"resort_name": "Westgate"}') == {"resort_name": "Westgate"}
    assert parse_metadata('{"a": null, "b": 7}') == {"b": "7"}
