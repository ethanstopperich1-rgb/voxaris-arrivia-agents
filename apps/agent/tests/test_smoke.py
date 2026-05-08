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
    # Pronunciation guide present (canonical port locks "uh-RIV-ee-uh")
    assert "uh-riv-ee-uh" in text


def test_persona_uses_generic_premium_language() -> None:
    """Per Stacey: never name a specific brand (Disney, etc.) for the offer.
    The persona/greeting must reference the offer via the {premium_offer}
    template variable so per-call substitution is brand-agnostic."""
    from voxaris_agent.worker import (
        DEFAULT_GUEST_CONTEXT,
        GREETING_INSTRUCTIONS_INBOUND_TEMPLATE,
        GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE,
        PERSONA_INSTRUCTIONS_TEMPLATE,
        render_greeting,
        render_persona,
    )

    # The TEMPLATES (pre-substitution) must reference the offer via the
    # placeholder so the call-site can swap brands at dispatch time.
    assert "{premium_offer}" in PERSONA_INSTRUCTIONS_TEMPLATE
    assert "{premium_offer}" in GREETING_INSTRUCTIONS_INBOUND_TEMPLATE
    assert "{premium_offer}" in GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE

    # The RENDERED greeting must not contain banned brand-specific terms.
    greeting = render_greeting()
    persona = render_persona()
    for banned in ("Disney", "park hopper", "Universal", "Marriott"):
        assert banned not in greeting, f"banned brand surfaced in greeting: {banned}"
        assert banned not in persona, f"banned brand surfaced in persona: {banned}"

    # Default guest context must not pre-bind a specific brand.
    assert "Disney" not in DEFAULT_GUEST_CONTEXT["premium_offer"]
    assert "park hopper" not in DEFAULT_GUEST_CONTEXT["premium_offer"]


def test_persona_lists_all_eight_canonical_hard_qual_checks() -> None:
    """Per Cassie OPC Script v2.0 (May 2026) — 8 hard-qualify checks
    in PHASE 4 (was 9 with local-market in pre-v2.0; v2.0 moves
    local-market to PHASE 3 soft-qualify). White-labeled into Deedy."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    # Eight hard-qualify checks, plus PHASE 3 local-market check.
    # The script (Stacey, May 2026) uses the digit "25" in the age check.
    # Income/$50K is spelled out per output_rules. Both forms are accepted.
    for check in (
        ("over 25", "twenty-five"),         # 1. Age 25+
        ("decision-makers",),               # 2. Both attending
        ("fifty thousand",),                # 3. Income $50K+
        ("credit card",),                   # 4. Major CC (yes/no)
        ("employed",),                      # 5. Employment
        ("last year",),                     # 6. Tour history
        ("open promotional",),              # 7. Open packages
        ("english",),                       # 8. Language self-check
        ("local market",),                  # PHASE 3 soft-qualify gate
    ):
        assert any(c in text for c in check), f"missing canonical check: any of {check!r}"


def test_persona_has_pii_prohibition() -> None:
    """v2.0 calls it 'PII prohibition' (covers PCI + SSN + DOB + DL)."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "pii prohibition" in text
    assert "credit card number" in text
    assert "social security number" in text
    # Card-digit interrupt phrase must be present
    assert "don't read that to me" in text or "don't read that" in text


def test_persona_has_golden_rule_on_disqualifies() -> None:
    """Per Cassie OPC v2.0: Cassie/Deedy NEVER says 'you don't qualify.'
    Always frames as fit + alternative. Caller hangs up feeling respected."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "golden rule" in text
    # Split into two adjacency checks so quote-style differences don't break it
    assert "never says" in text
    assert "you don't qualify" in text
    assert "fit problem" in text
    assert "respected" in text


def test_persona_5_phase_flow_intact() -> None:
    """Canonical OPC v2.0 replaces the legacy 22-node flow with 5 named
    phases. The phase headers must all be present in the prompt."""
    from voxaris_agent.worker import render_persona

    text = render_persona()  # not flattened — phase headers are upper-case
    for phase in (
        "PHASE 1",
        "PHASE 2",
        "PHASE 3",
        "PHASE 4",
        "PHASE 5",
        "INTRO & HOOK",
        "RAPPORT",
        "SOFT QUALIFY",
        "HARD QUALIFY",
        "CLOSE",
        "KEY FRAMING",
        "GOLDEN RULE",
        "THREE-STRIKE RULE",
    ):
        assert phase in text, f"missing canonical phase marker: {phase}"


def test_persona_includes_folio_deposit() -> None:
    """v1 is on-property only; folio handles deposit. The deposit
    framing must appear so any LLM in the fallback chain renders it
    consistently."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "folio" in text
    assert "seventy-five dollar" in text
    assert "comes right back off" in text
    # On-property scope explicit
    assert "on-property guests" in text or "on-property only" in text


def test_persona_names_agent_deedy() -> None:
    """The persona uses canonical "Deedy" (referenced by name); the
    greeting uses phonetic "Deedee" so Rime TTS pronounces it as a
    name not letters."""
    from voxaris_agent.worker import render_greeting, render_persona

    assert "Deedy" in render_persona()
    # Greeting uses "Deedee" phonetic spelling for TTS
    rendered_greeting = render_greeting()
    assert "Deedee" in rendered_greeting or "Deedy" in rendered_greeting


def test_inbound_greeting_uses_canonical_opener() -> None:
    """v2.0 opener: 'Hi, this is Deedee — I'm a virtual booking agent...'
    Names the offer. No 'thanks for calling' (legacy pre-v2.0 wording)."""
    from voxaris_agent.worker import render_greeting

    inbound = render_greeting({"direction": "inbound"}).lower()
    # Canonical v2.0 opener wording. "Deedy" and "Arrivia" now pass
    # through to the tts_node which rewrites them to the Rime
    # bracket-phonetic ({1di0di} and {0xr1Iv0i0x}) before audio
    # synthesis — so the LLM-facing instruction text just uses the
    # natural English spellings now.
    assert "deedy" in inbound or "deedee" in inbound
    assert "arrivia" in inbound
    assert "virtual booking agent" in inbound
    # Names the offer hook (v2.0 critical change — caller already knows
    # they want it; skip fishing)
    assert "claiming your" in inbound or "interested in" in inbound
    # Inbound must NOT say outbound-only phrasing
    assert "i'm calling on behalf of" not in inbound
    assert "thanks for scanning" not in inbound


def test_outbound_greeting_says_im_calling() -> None:
    """Outbound greeting must include 'I'm calling on behalf of'
    or similar outbound-direction language."""
    from voxaris_agent.worker import render_greeting

    outbound = render_greeting({"direction": "outbound"}).lower()
    assert "i'm calling" in outbound or "calling on" in outbound


def test_persona_lists_all_real_tools() -> None:
    """Tools per Cassie OPC v2.0 white-labeled into Deedy.
    'opc_book' is Deedy's booking primitive (Cassie calls it 'book_tour').
    'transfer_to_human' is Deedy's escalation tool (Cassie: 'escalate_to_human').
    Other tools are common."""
    from voxaris_agent.worker import render_persona

    text = render_persona()
    for tool in (
        "lookup_qa",
        "lookup_objection",
        "verify_me_to_caller",
        "note_uncertainty",
        "send_sms_confirmation",
        "opc_book",
        "transfer_to_human",
        "hangup_call",
    ):
        assert tool in text, f"missing tool reference: {tool}"


def test_persona_has_three_strike_rule() -> None:
    """v2.0 replaces the legacy two-strike rule + 'before you hedge'
    escalation with a clear three-strike rule on objections."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    assert "three-strike rule" in text
    assert "third time" in text or "third strike" in text
    # note_uncertainty + transfer_to_human are still tool-level escalation
    assert "note_uncertainty" in text
    assert "transfer_to_human" in text


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
    """Cross-model conformance: regardless of which LLM the fallback
    chain lands on (gpt-4o-mini / gpt-4.1-mini / grok-4.20), these
    invariants must be in the prompt so every model is told the same
    hard constraints. Aligned to Cassie OPC v2.0 — same baseline
    that powers the canonical Cassie deployment."""
    from voxaris_agent.worker import render_persona

    text = _flat(render_persona())
    # Identity invariants (must survive model switch)
    assert "deedy" in text  # name preserved
    assert "arrivia" in text  # platform brand preserved
    # Compliance invariants (canonical names)
    assert "never claim to be human" in text or "never deny being ai" in text
    assert "pii prohibition" in text          # was "absolute prohibition" pre-v2.0
    assert "golden rule" in text              # canonical disqual principle
    assert "three-strike rule" in text        # canonical objection limit
    # Tool-call invariants — these are how we detect non-conformance
    for required in (
        "lookup_qa",
        "lookup_objection",
        "opc_book",
        "send_sms_confirmation",
        "transfer_to_human",
        "note_uncertainty",
        "verify_me_to_caller",
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
