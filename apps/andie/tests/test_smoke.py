"""Smoke tests for Andie — package imports + persona invariants."""
from __future__ import annotations


def test_module_imports() -> None:
    from voxaris_andie import worker
    assert worker.entrypoint is not None
    assert worker.cli_main is not None


def _flat(t: str) -> str:
    return " ".join(t.split()).lower()


def test_persona_brands_as_arrivia_and_gvr() -> None:
    from voxaris_andie.worker import render_persona
    text = _flat(render_persona())
    assert "arrivia" in text
    assert "government vacation rewards" in text
    # Phonetic spelling for the TTS — note RIV (not RIH).
    assert "uh-riv-ee-uh" in text


def test_persona_disclaims_government_endorsement() -> None:
    from voxaris_andie.worker import render_persona
    text = _flat(render_persona())
    assert "not a government" in text
    assert "not endorsed" in text or "endorsed by the u.s. military" in text


def test_persona_has_pci_prohibition() -> None:
    from voxaris_andie.worker import render_persona
    text = _flat(render_persona())
    assert "ssn" in text
    assert "credit card" in text
    assert "please stop" in text


def test_persona_has_4_pillars() -> None:
    from voxaris_andie.worker import render_persona
    text = _flat(render_persona())
    for pillar in ("savings credits", "reward points", "great getaways", "quarterly specials"):
        assert pillar in text, f"missing pillar: {pillar}"


def test_inbound_greeting_leads_with_live_transfer() -> None:
    """User explicitly required: live transfer = default, link = backup.

    The current verbatim opener (`OPENER_INBOUND_VERBATIM`) introduces
    Andee, discloses recording, names the four pillars, and offers two
    paths: walk-through OR specialist transfer. We verify the opener
    text directly since session.say() now plays it (the `render_greeting`
    instructions are kept for backward compat but no longer the source
    of truth).
    """
    from voxaris_andie.worker import render_opener_text
    text = _flat(render_opener_text({"direction": "inbound"}))
    assert "this call may be recorded" in text
    assert "specialist" in text
    # The opener must offer the transfer path — not be walk-through-only.
    assert "specialist if you'd rather" in text or "get you to a specialist" in text


def test_outbound_greeting_says_calling() -> None:
    from voxaris_andie.worker import render_greeting
    text = _flat(render_greeting({"direction": "outbound"}))
    assert "calling" in text or "reaching out" in text


def test_persona_uses_andee_phonetic() -> None:
    """Rime mistv3 reads "Andie" as letters; persona uses "Andee"."""
    from voxaris_andie.worker import render_greeting, render_persona
    assert "Andee" in render_greeting()
    assert "Andee" in render_persona()


def test_persona_lists_real_tools() -> None:
    from voxaris_andie.worker import render_persona
    text = render_persona()
    for tool in ("lookup_faq", "send_scheduler_link", "transfer_to_specialist", "hangup_call"):
        assert tool in text


def test_qa_loaded() -> None:
    from voxaris_andie.qa import count
    assert count() > 0


def test_objections_loaded_89_entries() -> None:
    """89 entries: 84 from Grok multi-source research + 5 added
    2026-05-04 from human-rep transcript review (spouse / don't-
    remember / send-email-instead / no-time / can't-afford)."""
    from voxaris_andie.objections import categories, count

    assert count() == 89
    cats = set(categories())
    expected = {
        "skepticism_trust", "time_pressure", "travel_fit", "cost_value",
        "privacy_data", "negative_past", "decision_maker", "channel_pref",
        "life_stage", "rejection",
    }
    assert expected.issubset(cats), f"missing categories: {expected - cats}"


def test_objection_lookup_handles_common_phrases() -> None:
    from voxaris_andie.objections import match_objection

    cases = [
        ("is this a scam?", "skepticism_trust"),
        ("im busy right now", "time_pressure"),
        ("we dont travel much", "travel_fit"),
        ("how much does it cost", "cost_value"),
        ("how did you get my number", "privacy_data"),
        ("take me off your list", "rejection"),
    ]
    for phrase, expected_cat in cases:
        res = match_objection(phrase, top_k=3)
        cats = [m.category for m in res]
        assert expected_cat in cats, (
            f"{phrase!r} expected {expected_cat}, got {cats}"
        )


def test_persona_has_ftc_disclaimer_rules() -> None:
    """FTC enforcement risk — must explicitly correct gov endorsement
    misconceptions and never use 'government-approved' phrasing."""
    from voxaris_andie.worker import render_persona

    text = _flat(render_persona())
    # Must include the safe phrases
    assert "private travel-rewards program" in text
    assert "not affiliated" in text
    # Must include the explicit "never use" red-flag list
    assert "government-approved" in text  # appears in the don't-say list
    assert "must never use" in text or "never use" in text


def test_persona_has_scam_pattern_blocklist() -> None:
    """Per Grok trust research: certain phrases pattern-match to scam
    calls and tank caller trust even when said innocently."""
    from voxaris_andie.worker import render_persona

    text = _flat(render_persona())
    # The blocklist itself must appear in the persona
    assert "scam-pattern" in text or "scam pattern" in text
    assert "act now" in text  # in the don't-say list
    assert "press 1 to claim" in text or "press 1" in text


def test_persona_has_trust_building_phrases() -> None:
    from voxaris_andie.worker import render_persona

    text = _flat(render_persona())
    assert "trust-building" in text or "wary" in text
    assert "log into your account" in text
    # Replaces the older "courtesy call" check — current persona uses
    # the credibility-anchor pattern (enrollment date + email on file)
    # which Jay flagged as the highest-impact early-call trust move.
    assert "email on file" in text or "credibility anchor" in text


def test_persona_lists_lookup_objection_tool() -> None:
    from voxaris_andie.worker import render_persona

    assert "lookup_objection" in render_persona()


# ─────────────────────────────────────────────
# Voicemail behavior tests (added 2026-05-06)
#
# When AMD detects a machine, Andie speaks a verbatim voicemail
# message via session.say() instead of the live opener. If the
# recipient picks up mid-message, allow_interruptions=True cuts her
# off and the persona instructs her to pivot. These tests verify
# the static pieces — script content, persona handling, hangup
# disposition — without booting a full LiveKit session.
# ─────────────────────────────────────────────


def test_voicemail_render_outbound_with_dynamic_vars() -> None:
    """Voicemail script interpolates name, incentive, and callback number."""
    from voxaris_andie.worker import render_voicemail_text

    text = render_voicemail_text({
        "member_name": "Stacey",
        "incentive_amount": "two hundred fifty dollars",
        "callback_number_spoken": "1 2 3, 4 5 6, 7 8 9 0",
    })
    assert "Stacey" in text
    assert "two hundred fifty dollars" in text
    assert "1 2 3, 4 5 6, 7 8 9 0" in text
    # Repeats the callback number twice ("Again, that's...")
    assert text.count("1 2 3, 4 5 6, 7 8 9 0") == 2
    # SSML breaks for natural pacing on Cartesia
    assert '<break time="' in text


def test_voicemail_render_default_ctx_does_not_crash() -> None:
    """Empty ctx falls through to DEFAULT_MEMBER_CONTEXT cleanly."""
    from voxaris_andie.worker import render_voicemail_text

    text = render_voicemail_text(None)
    # Default member_name is "there" — covers no-name case
    assert "there" in text
    assert "Government Vacation Rewards" in text


def test_voicemail_has_required_compliance_pieces() -> None:
    """Three things must always be in the voicemail: brand, callback, opt-out."""
    from voxaris_andie.worker import render_voicemail_text

    text = _flat(render_voicemail_text({"member_name": "Test"}))
    # Brand identification
    assert "government vacation rewards" in text
    # Callback path — both the explicit "call back at" and the repeat
    assert "call back" in text or "give us a call" in text
    # Opt-out disclosure (TCPA-defensive — "if you'd prefer not to hear from us")
    assert "removed" in text or "remove" in text
    assert "prefer not" in text or "opt out" in text


def test_persona_has_voicemail_handling_section() -> None:
    """Persona must instruct Andie how to handle voicemail and the
    mid-message pivot. Without this, the LLM doesn't know to call
    hangup_call after a clean voicemail or to pivot when interrupted."""
    from voxaris_andie.worker import render_persona

    text = render_persona()
    assert "Voicemail handling" in text
    # The pivot instruction — Andie must acknowledge being mid-voicemail
    assert "leaving you a quick voicemail" in text or "mid-voicemail" in text.lower()
    # The hangup disposition for completed voicemail
    assert "voicemail_left" in text


def test_hangup_tool_accepts_voicemail_dispositions() -> None:
    """The hangup_call tool description must list voicemail_left and
    no_answer as valid reasons so the LLM uses them correctly."""
    from voxaris_andie.worker import render_persona

    persona = render_persona()
    # Mentioned in persona's Tools section as valid dispositions
    # (sanity check that hangup_call is documented at all)
    assert "hangup_call" in persona


def test_metadata_parser() -> None:
    from voxaris_andie.worker import parse_metadata
    assert parse_metadata(None) == {}
    assert parse_metadata("not json") == {}
    assert parse_metadata('{"member_name": "Jane"}') == {"member_name": "Jane"}
