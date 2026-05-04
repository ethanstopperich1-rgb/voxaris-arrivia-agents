"""Smoke tests — module imports and persona invariants.

Persona: ported from Retell flow + revised per Stacey's feedback
2026-05-03 (Virtual Booking Agent branding, Arrivia platform brand,
generic premium offer language, 18+ data-consent gate, soft pushback
handling).
"""

from __future__ import annotations


def test_module_imports() -> None:
    from voxaris_agent import worker

    assert worker.entrypoint is not None
    assert worker.cli_main is not None


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def test_persona_acknowledges_ai_when_asked() -> None:
    """Per QA v2 'Coaching reminders': never deny AI — but call yourself
    Virtual Booking Agent."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "virtual booking agent" in text
    assert "never deny" in text or "ai-powered" in text


def test_persona_brands_as_arrivia_not_resort() -> None:
    """Per Stacey: brand the platform Arrivia, not the resort."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "arrivia" in text
    # Pronunciation guide present (Stacey corrected to uh-RIH-vee-uh)
    assert "uh-rih-vee-uh" in text


def test_persona_uses_generic_premium_language() -> None:
    """Per Stacey: never name Disney tickets or specific premium."""
    from voxaris_agent.worker import render_greeting, render_persona

    persona = render_persona()
    greeting = render_greeting()
    # The generic phrase must appear in both
    assert "premium" in persona.lower()
    assert "premium" in greeting.lower()
    # The default greeting must NOT mention Disney (Stacey: don't name
    # the specific premium on the call). The persona may reference
    # Disney as an explicit don't-say example, that's fine.
    assert "Disney" not in greeting
    assert "park hopper" not in greeting
    # Default guest context must use generic offer language
    from voxaris_agent.worker import DEFAULT_GUEST_CONTEXT
    assert "Disney" not in DEFAULT_GUEST_CONTEXT["premium_offer"]
    assert "premium" in DEFAULT_GUEST_CONTEXT["premium_offer"].lower()


def test_persona_lists_all_nine_canonical_gates() -> None:
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    for gate in (
        "twenty-five or older",
        "fifty thousand dollars",
        "decision-makers",
        "credit card",
        "active bankruptcy",
        "employed, self-employed",
        "six to twelve months",
        "central florida",
        "english",
        "90-minute preview",
    ):
        assert gate in text, f"missing canonical gate: {gate!r}"


def test_persona_has_18_plus_data_consent_gate() -> None:
    """Per Stacey + COPPA: confirm 18+ BEFORE collecting any data."""
    from voxaris_agent.worker import render_greeting, render_persona

    persona = _flat(render_persona())
    greeting = _flat(render_greeting())
    assert "eighteen" in persona or "18+" in persona or "18 or older" in persona
    assert "eighteen" in greeting or "18" in greeting
    assert "before collecting" in persona or "before" in persona


def test_persona_has_pci_absolute_prohibition() -> None:
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "absolute prohibition" in text
    assert "credit card number" in text
    assert "cvv" in text
    assert "please stop" in text


def test_persona_handles_pushback_softly() -> None:
    """Per Stacey: she shouldn't drop the call when challenged."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    # The revised "handling pushback" rules must be present
    assert "factual pushback" in text or "factual challenges" in text
    assert "do not drop" in text or "not drop the call" in text


def test_persona_22_node_flow_intact() -> None:
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    for node in (
        "start_disclosures",
        "hook_and_permission",
        "soft_qual",
        "hard_qual_age",
        "hard_qual_decision_makers",
        "hard_qual_income",
        "hard_qual_employment",
        "hard_qual_credit",
        "hard_qual_prior_tour",
        "hard_qual_residency",
        "hard_qual_language",
        "hard_qual_attendance",
        "schedule_offer",
        "deposit_explanation",
        "confirm_and_sms_consent",
        "book_tool_call",
        "end_confirmed_tour",
        "end_graceful",
        "obj_time",
        "obj_sales",
        "obj_spouse",
        "obj_general",
    ):
        assert node in text, f"missing flow node: {node}"


def test_persona_includes_deposit_branching() -> None:
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "on_property is true" in text
    assert "on_property is false" in text
    assert "folio" in text


def test_persona_names_agent_deedy() -> None:
    """The persona uses canonical "Deedy" (referenced by name); the
    greeting uses phonetic "Deedee" so Rime TTS pronounces it as a
    name not letters."""
    from voxaris_agent.worker import render_greeting, render_persona

    assert "Deedy" in render_persona()
    # Greeting uses "Deedee" phonetic spelling for TTS
    rendered_greeting = render_greeting()
    assert "Deedee" in rendered_greeting or "Deedy" in rendered_greeting


def test_inbound_greeting_does_not_say_im_calling() -> None:
    """Inbound calls must not have outbound phrasing."""
    from voxaris_agent.worker import render_greeting

    inbound = render_greeting({"direction": "inbound"}).lower()
    assert "thanks for calling" in inbound
    assert "scanned" not in inbound  # only outbound mentions scanning


def test_outbound_greeting_says_im_calling() -> None:
    from voxaris_agent.worker import render_greeting

    outbound = render_greeting({"direction": "outbound"}).lower()
    assert "i'm calling" in outbound or "calling on" in outbound


def test_persona_lists_all_real_tools() -> None:
    from voxaris_agent.worker import render_persona

    text = render_persona()
    for tool in (
        "lookup_qa",
        "lookup_objection",
        "opc_book",
        "send_sms_confirmation",
        "hangup_call",
        "note_uncertainty",
        "transfer_to_human",
    ):
        assert tool in text, f"missing tool reference: {tool}"


def test_persona_has_escalation_policy() -> None:
    """Three triggers: 2x no_match, 2x hedge, 3x repeat-question."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "escalation policy" in text
    assert "note_uncertainty" in text
    assert "transfer_to_human" in text
    # The "before you hedge" rule must be explicit
    assert "before you hedge" in text


def test_lookup_qa_escalates_after_two_no_match() -> None:
    """Two consecutive lookup_qa no_matches must return escalate=True
    with an instruction to call transfer_to_human."""
    import asyncio

    from voxaris_agent.worker import VBAQualifierAgent

    a = VBAQualifierAgent()
    # Two clearly off-topic questions in a row
    r1 = asyncio.run(a.lookup_qa("what's the weather in mars"))
    r2 = asyncio.run(a.lookup_qa("how do submarines work"))
    assert r1["no_match"] is True
    assert r2["no_match"] is True
    assert r2.get("escalate") is True
    assert "transfer_to_human" in r2.get("instruction", "").lower()


def test_lookup_qa_streak_resets_on_match() -> None:
    """A successful match resets the no_match streak."""
    import asyncio

    from voxaris_agent.worker import VBAQualifierAgent

    a = VBAQualifierAgent()
    asyncio.run(a.lookup_qa("what's the weather"))  # no_match #1
    asyncio.run(a.lookup_qa("how long is the presentation"))  # match resets
    r3 = asyncio.run(a.lookup_qa("what's mars like"))  # no_match #1 again
    assert r3.get("no_match") is True
    # Should NOT be escalating yet — streak was reset
    assert not r3.get("escalate", False)


def test_persona_conformance_invariants_for_fallback() -> None:
    """Per Claude Opus's warning about personality drift across the
    fallback LLM chain: regardless of which model we land on, certain
    invariants MUST hold in every rendered response. This test
    verifies the persona text itself encodes those invariants so any
    LLM in the chain (Grok 4.20 / 4.1-fast / GPT-4.1-mini) is told
    the same hard constraints."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    # Identity invariants (must survive model switch)
    assert "deedy" in text  # name preserved
    assert "arrivia" in text  # platform brand preserved
    # Compliance invariants
    assert "never claim to be human" in text
    assert "absolute prohibition" in text  # PCI rule
    assert "eighteen" in text  # 18+ data gate
    # Tool-call invariants — these are how we detect non-conformance
    # at runtime if a fallback model starts misbehaving
    for required in (
        "lookup_qa",
        "lookup_objection",
        "opc_book",
        "send_sms_confirmation",
        "transfer_to_human",
        "note_uncertainty",
    ):
        assert required in text, f"persona missing tool ref: {required}"


def test_note_uncertainty_escalates_after_two() -> None:
    """Two consecutive note_uncertainty calls must trigger escalation."""
    import asyncio

    from voxaris_agent.worker import VBAQualifierAgent

    a = VBAQualifierAgent()
    r1 = asyncio.run(a.note_uncertainty("not sure about pricing"))
    r2 = asyncio.run(a.note_uncertainty("not sure about timing"))
    assert r1.get("escalate") is False
    assert r2.get("escalate") is True
    assert "transfer_to_human" in r2.get("instruction", "").lower()


def test_metadata_parser_handles_garbage() -> None:
    from voxaris_agent.worker import parse_metadata

    assert parse_metadata(None) == {}
    assert parse_metadata("") == {}
    assert parse_metadata("not json at all") == {}
    assert parse_metadata('{"property_name": "Westgate"}') == {"property_name": "Westgate"}
    assert parse_metadata('{"a": null, "b": 7}') == {"b": "7"}


def test_qa_dataset_loads_18_entries() -> None:
    from voxaris_agent.qa import count, sections, match_qa

    assert count() == 18
    assert "the free premium" in sections()
    assert "the presentation" in sections()
    assert "the deposit & booking" in sections()
    assert "qualifying for the presentation" in sections()


def test_qa_lookup_handles_common_questions() -> None:
    from voxaris_agent.qa import match_qa

    # "how long is the presentation"
    res = match_qa("how long is the presentation")
    assert res, "no match for presentation length question"
    assert "90" in res[0].answer or "ninety" in res[0].answer.lower()

    # "do both spouses need to attend"
    res = match_qa("do both spouses need to attend")
    assert res, "no match for spouse attendance question"
    assert "both" in res[0].answer.lower()
