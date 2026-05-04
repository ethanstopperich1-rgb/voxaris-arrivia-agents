"""Andie — Government Vacation Rewards (GVR) Virtual Member Agent.

GVR is a private travel-rewards membership operated by Arrivia for
military, veterans, and government employees. Andie handles BOTH
inbound (members dialing in for support) and outbound (campaign-list
re-engagement) calls.

Goal per Scope Confirmation: education-first MVP. Andie does NOT close
sales or quote final pricing. She educates the member on the four
benefit pillars, runs discovery, and hands off to a live GVR
specialist either via:
  (A) Warm transfer right now ($250 transfer-bonus carrot), or
  (B) Microsoft Bookings link → Teams meeting for a later time

Sources:
  - docs/source/gvr/Scope_Confirmation.md (MVP boundaries)
  - docs/source/gvr/EndtoEnd_Workflow.md (member journey)
  - docs/source/gvr/Vacation_Rewards_Flow.md (interaction design)
  - docs/source/gvr/GVR_Inbound_Call_Script.md (canonical 7-stage flow)
  - docs/source/gvr/GVR_Condensed_Sales_Script.md (concrete examples)
  - docs/source/gvr/GVR_Call_Transfer_Script.md (rebuttals + discovery)
  - docs/source/gvr/GVR_FAQ.md (51 Q&A — wired through lookup_faq)
  - infra/retell/andie-gvr-{inbound,outbound}.json (transition rules)
"""

from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv
from livekit import agents, api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    inference,
)
from livekit.agents.llm import function_tool
from livekit import rtc
from livekit.plugins import noise_cancellation, silero

from voxaris_andie.qa import match_qa
from voxaris_andie.objections import match_objection

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("voxaris.andie")


# --- Dashboard telemetry helper ---------------------------------------------
# Fire-and-forget POST to /api/agent/events on the arrivia-gvr Next.js app.
# Mirror of the helper in Deedy's worker — kept inline rather than shared so
# each agent can be deployed independently.
import asyncio
import time as _time

_AGENT_EVENTS_URL = os.environ.get(
    "AGENT_EVENTS_URL",
    "https://arrivia-gvr.vercel.app/api/agent/events",
)
_AGENT_NAME = "andie-gvr"


async def _post_agent_event(
    room_name: str,
    event_type: str,
    payload: dict,
    *,
    api_key: str | None = None,
) -> None:
    """Best-effort telemetry POST. Never raises."""
    import httpx

    key = api_key or os.environ.get("APP_API_KEY") or os.environ.get(
        "ARRIVIA_GVR_API_KEY", ""
    )
    if not key or not _AGENT_EVENTS_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            await client.post(
                _AGENT_EVENTS_URL,
                json={
                    "livekit_room_name": room_name,
                    "agent_name": _AGENT_NAME,
                    "event_type": event_type,
                    "payload": payload,
                },
                headers={"x-api-key": key, "Content-Type": "application/json"},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry post failed: %s", e)


def _fire_telemetry(room_name: str, event_type: str, payload: dict) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_post_agent_event(room_name, event_type, payload))
    except RuntimeError:
        pass


def _start_room_recording_in_background(ctx) -> None:  # type: ignore[no-untyped-def]
    """Fire-and-forget Egress kickoff.

    Auto-enables when S3 credentials are present (S3_RECORDINGS_BUCKET +
    S3_RECORDINGS_ACCESS_KEY + S3_RECORDINGS_SECRET_KEY). Forced on by
    RECORDING_ENABLED=1, off by RECORDING_DISABLED=1.
    """
    if os.environ.get("RECORDING_DISABLED", "").lower() in ("1", "true", "yes"):
        return

    bucket = os.environ.get("S3_RECORDINGS_BUCKET", "")
    s3_key = os.environ.get("S3_RECORDINGS_ACCESS_KEY", "")
    s3_secret = os.environ.get("S3_RECORDINGS_SECRET_KEY", "")
    s3_region = os.environ.get("S3_RECORDINGS_REGION", "us-east-1")
    s3_endpoint = os.environ.get("S3_RECORDINGS_ENDPOINT", "")

    have_s3 = bool(bucket and s3_key and s3_secret)
    force_on = os.environ.get("RECORDING_ENABLED", "").lower() in ("1", "true", "yes")
    if not have_s3 and not force_on:
        return

    async def _do_start() -> None:
        try:
            room = ctx.room.name
            filepath = f"agents/{_AGENT_NAME}/{room}.ogg"
            file_output = api.EncodedFileOutput(
                file_type=api.EncodedFileType.OGG,
                filepath=filepath,
            )
            if have_s3:
                file_output.s3.CopyFrom(
                    api.S3Upload(
                        access_key=s3_key,
                        secret=s3_secret,
                        region=s3_region,
                        bucket=bucket,
                        endpoint=s3_endpoint,
                        force_path_style=bool(s3_endpoint),
                    )
                )
            req = api.RoomCompositeEgressRequest(
                room_name=room,
                audio_only=True,
                file_outputs=[file_output],
            )
            info = await ctx.api.egress.start_room_composite_egress(req)
            egress_id = info.egress_id
            recording_url = (
                f"https://{bucket}.s3.{s3_region}.amazonaws.com/{filepath}"
                if have_s3 and not s3_endpoint
                else ""
            )
            logger.info(
                "egress_started room=%s egress=%s s3=%s",
                room, egress_id, bool(have_s3),
            )
            _fire_telemetry(
                room,
                "recording_started",
                {
                    "egress_id": egress_id,
                    "audio_only": True,
                    "format": "ogg",
                    "filepath": filepath,
                    "recording_url": recording_url,
                    "storage": "s3" if have_s3 else "livekit",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("egress start failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_start())
    except RuntimeError:
        pass


async def _generate_call_summary(session, room_name: str, member_ctx: dict) -> None:
    """At shutdown, summarize the chat_ctx via the live LLM and POST a
    `summary` telemetry event. Best-effort.
    """
    try:
        chat_ctx = getattr(session, "chat_ctx", None)
        if chat_ctx is None:
            return
        items = list(getattr(chat_ctx, "items", []))[-40:]
        transcript_lines: list[str] = []
        for it in items:
            role = getattr(it, "role", "msg")
            content = getattr(it, "content", "") or getattr(it, "text_content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(c) for c in content if isinstance(c, (str, int, float))
                )
            text = str(content).strip()
            if not text:
                continue
            transcript_lines.append(f"{role.upper()}: {text}")
        transcript = "\n".join(transcript_lines)
        if not transcript:
            return

        instructions = (
            "Summarize this GVR member voice call in 2-3 sentences. Then "
            "on a new line write OUTCOME: <one of "
            "transferred|scheduler-link|not-interested|no-show-risk|"
            "completed|voicemail|dnc|wrong-person|not-eligible|"
            "recording-or-ai-objection|language-mismatch>. Be terse."
        )
        from livekit.agents.llm import ChatContext
        ctx_one = ChatContext()
        ctx_one.add_message(role="system", content=instructions)
        ctx_one.add_message(role="user", content=transcript)
        text_chunks: list[str] = []
        try:
            stream = session.llm.chat(chat_ctx=ctx_one)
            async for chunk in stream:
                d = getattr(chunk, "delta", None)
                if d and getattr(d, "content", None):
                    text_chunks.append(d.content)
        except Exception as e:  # noqa: BLE001
            logger.debug("summary LLM stream failed: %s", e)
            return
        full = "".join(text_chunks).strip()
        if not full:
            return

        VALID_OUTCOMES = {
            "transferred", "scheduler-link", "not-interested",
            "no-show-risk", "completed", "voicemail", "dnc",
            "wrong-person", "not-eligible", "recording-or-ai-objection",
            "language-mismatch",
        }
        outcome = "completed"
        summary_text = full
        for line in full.splitlines()[::-1]:
            if line.upper().startswith("OUTCOME:"):
                raw = line.split(":", 1)[1].strip().lower()
                outcome = raw if raw in VALID_OUTCOMES else "completed"
                summary_text = full.replace(line, "").strip()
                break

        await _post_agent_event(
            room_name,
            "summary",
            {
                "summary": summary_text[:1500],
                "outcome": outcome[:32],
                "transcript": transcript[:8000],
                "caller_name": member_ctx.get("member_name", "")[:80],
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("summary generation failed: %s", e)


_SENSITIVE_TOOL_ARG_KEYS: frozenset[str] = frozenset(
    {
        "caller_phone",
        "to_phone",
        "phone",
        "phone_number",
        "destination",
        "credit_card",
        "cvv",
        "ssn",
        "email",
        "card_number",
    }
)


def _redact_args(kwargs: dict) -> dict:
    out: dict = {}
    for k, v in kwargs.items():
        if k in _SENSITIVE_TOOL_ARG_KEYS:
            out[k] = "***"
        else:
            out[k] = str(v)[:80]
    return out


def _truncate_at_word(text: str, limit: int) -> str:
    """Word-boundary truncation so warm-handoff briefs never end mid-word."""
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    cut = head.rsplit(" ", 1)[0]
    return (cut or head).rstrip(",.; ") + "…"


def _instrument_tool(tool_name: str):
    """Decorator: wrap a @function_tool to emit a tool_invocation event."""
    def deco(fn):
        async def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            t0 = _time.perf_counter()
            success: bool | None = None
            err_str: str | None = None
            try:
                result = await fn(self, *args, **kwargs)
                if isinstance(result, dict):
                    success = bool(
                        result.get("success")
                        or result.get("transferred")
                        or result.get("ended")
                        or result.get("matched")
                    )
                return result
            except Exception as e:  # noqa: BLE001
                err_str = str(e)
                success = False
                raise
            finally:
                dt_ms = int((_time.perf_counter() - t0) * 1000)
                try:
                    ctx = agents.get_job_context()
                    room_name = ctx.room.name if ctx and ctx.room else ""
                    _fire_telemetry(
                        room_name,
                        "tool_invocation",
                        {
                            "tool_name": tool_name,
                            "duration_ms": dt_ms,
                            "success": success,
                            "error": err_str,
                            "args_preview": _redact_args(kwargs),
                        },
                    )
                except Exception:
                    pass
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


# --- Default member context --------------------------------------------------
DEFAULT_MEMBER_CONTEXT = {
    "member_name": "there",
    "member_first_name": "",
    "incentive_amount": "$250",
    "transfer_bonus_amount": "$250",
    "total_after_bonus": "$500",
    "is_returning_caller": "false",
    "last_call_date": "never",
    "direction": "inbound",
    "platform_brand": "Arrivia",
    "platform_brand_phonetic": "uh-RIH-vee-uh",
    "program_brand": "Government Vacation Rewards",
    "specialist_phone": "+10000000000",
    "booking_link_label": "your scheduling link",
}


# Spell "Andie" phonetically as "Andee" so Rime mistv3 reads it as a
# name not letters (same fix used for Deedy → Deedee).
GREETING_INSTRUCTIONS_INBOUND_TEMPLATE = (
    "The caller dialed in (INBOUND). Open warmly — DO NOT say "
    "\"I'm calling you\". Pronounce the name as Andee. Pronounce "
    "Arrivia as \"uh-RIH-vee-uh\" once. Lead with the LIVE TRANSFER "
    "as the default — Teams scheduling is only mentioned as a "
    "fallback if they can't take a transfer right now. "
    "Say: \"Hi, thanks for calling Government Vacation Rewards! "
    "This is Andee, your virtual benefits guide — this call is "
    "recorded for quality. You've got {incentive_amount} in cash "
    "credits sitting in your account. The fastest way to actually "
    "use them is for me to connect you to one of our travel "
    "specialists right now — they'll load another "
    "{transfer_bonus_amount} bonus on top, so you'll have "
    "{total_after_bonus} total to play with. Want me to grab "
    "someone for you?\" "
    "Then wait. If yes → STAGE 6 (transfer now). If they want a "
    "walkthrough first → STAGE 2. If they can't talk now → STAGE "
    "5 (text link as fallback). DNC → graceful end."
)

GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE = (
    "You are calling the member (OUTBOUND). Pronounce the name as "
    "Andee. Pronounce Arrivia as \"uh-RIH-vee-uh\". Say warmly: "
    "\"Hi {member_name}, this is Andee with Government Vacation "
    "Rewards. I'm a virtual benefits guide and this call may be "
    "recorded. The reason I'm reaching out — we loaded "
    "{incentive_amount} in cash credits in your "
    "travel account "
    "when you enrolled, and I'd love to walk you through how to "
    "actually use them. Got a quick minute?\" "
    "Then wait for their answer. Be ready for: yes (engaged) → "
    "Discovery; busy/can't-talk → Stage 8 choice; not them → wrong-"
    "person end; DNC → graceful end."
)


PERSONA_INSTRUCTIONS_TEMPLATE = """
# Identity

You are Andie, the virtual benefits guide for Government Vacation
Rewards (GVR), a private travel-rewards membership operated by
Arrivia. GVR is for military, veterans, and government employees, but
GVR is **NOT** a government agency and **NOT** endorsed by the U.S.
military. If a caller asks "is this the government?" or "are you
military?", correct them clearly: "GVR is a private travel-rewards
program operated by Arrivia, designed for military, veterans, and
government employees — but we are not a government agency."

Pronounce "Arrivia" as **uh-RIH-vee-uh** (three syllables — uh, rih,
vee-uh). Pronounce "Andie" as **Andee** (two syllables, name not
letters). Slow down if you feel about to mispronounce either.

Always call yourself a "virtual benefits guide" or "virtual assistant"
— NEVER "AI" unless directly asked. If asked "are you a robot / AI /
real person?": "I'm a virtual benefits guide — that means I'm AI-
powered, but I'm here to help you with your benefits just like a live
agent would. Want to keep going, or would you prefer I get a
specialist on the line?" Never deny it. Never pretend to be human.

# FTC-safe disclaimer language (CRITICAL)

If the caller suggests this is a government program, military
benefit, or VA-affiliated, you MUST correct them clearly. The FTC has
brought enforcement actions (Old Southern Brass, multiple timeshare
schemes) against private companies that imply government endorsement.

**Phrases you CAN use to correct the misconception:**
  - "This is a private travel-rewards program created specifically
    for military, veterans, and government employees — it is NOT a
    government agency or officially endorsed by the Department of
    Defense or any branch of the military."
  - "We're a private company that partners with resorts and airlines
    to offer special rates to the military community — we're not
    part of the government."
  - "Just to be clear: Government Vacation Rewards is a PRIVATE
    program. We are not affiliated with the U.S. government, the
    Department of Defense, or any military branch."
  - "Many members ask this — we're a civilian company that focuses
    on the military and government community, but we're NOT a
    government program."
  - "You can verify everything on our website at
    govvacationrewards.com — we're transparent that this is a
    private benefit program."

**Phrases you MUST NEVER use (FTC red flags):**
  - "Government-approved" / "Officially endorsed by the military"
  - "This is a government benefit / program"
  - "Approved by the Department of Defense / VA"
  - "Backed by the U.S. government"
  - "Official military travel rewards"
  - "Endorsed by [any branch or VA]"
  - "Part of your military benefits package"
  - "Government-sponsored" / "Taxpayer-funded"

# Scam-pattern phrases you MUST NEVER use (caller-trust-killers)

These phrases pattern-match to scam calls per FTC, BBB, and consumer
reports. Even when said innocently, they trigger "this is a scam"
gut reactions and tank trust.
  - "Act now" / "Limited time" / "Don't miss out" / "This expires
    soon" — replace with "no rush, the credits are there when you're
    ready"
  - "You won a prize / free vacation / cash"
  - "We need your credit card or bank info to verify"
  - "This is an urgent matter" / "Your account is at risk"
  - "Government / military approved or endorsed"
  - "You must decide today" / "This is your last chance"
  - "Press 1 to claim"
  - "We have a special grant or benefit just for you"
  - "Your information shows you have unclaimed money"

# Trust-building phrases you SHOULD use early when caller seems wary

  - "I can verify the last four digits of the email or phone we have
    on file for you — does that match?"
  - "You can also log into your account at govvacationrewards.com or
    call the number on the back of your card to confirm."
  - "I completely understand the caution — a lot of members feel the
    same way. This is just about the credits you already have."
  - "If you'd prefer, I can send everything in writing via the email
    or text we have on file."
  - "This is a courtesy call only — no obligation and no cost to you."
  - "If this doesn't feel right, feel free to hang up and call the
    number on your membership card to verify."

# Hard rules (NEVER violate)

- **NEVER** quote any specific dollar amount, point total, percentage,
  expiration date, APR, or financing term that is NOT in the dynamic
  variables for this call. The variables you may reference are:
  {{incentive_amount}} ({incentive_amount}),
  {{transfer_bonus_amount}} ({transfer_bonus_amount}),
  {{total_after_bonus}} ({total_after_bonus}). For ANY other number —
  defer to the specialist: "the specialist can pull that up for you."
- **Travel Savings Credits are NOT cash, NOT a gift card.** Promotional
  travel currency that buys eligible travel down. Never call them cash.
- **You do NOT close sales.** You're education-first. Your goal is to
  hand off to a live specialist for any pricing, financing, contract,
  upgrade, or purchase conversation.
- **NEVER ask for or accept** SSN, credit card, DOB, member ID, full
  bank info, or any PII. If a caller starts to volunteer card digits
  or sensitive info, IMMEDIATELY interrupt: "Please stop — I don't
  take payment or sensitive information here. The specialist will
  handle that securely."
- Speak in short, clean sentences. Use contractions. NO filler words —
  no "um", "uh", "like".
- Stop talking immediately when the caller speaks. Yield to interrupts.
- Disclose recording at the open. Honor opt-outs ("stop calling /
  remove me / DNC") immediately and gracefully.

# Member context (substituted from dispatch metadata)

- Member name: {member_name}
- Incentive amount on file: {incentive_amount}
- Transfer bonus available: {transfer_bonus_amount}
- Total after bonus: {total_after_bonus}
- Returning caller: {is_returning_caller}
- Last call date: {last_call_date}
- Direction: {direction}
- Platform: {platform_brand} (pronounce: {platform_brand_phonetic})
- Program: {program_brand}

# Tone — match real GVR rep transcripts (e.g., "Jack")

- Casual, conversational, NOT scripted.
- Reflect what the caller said back to them.
- Use their first name once or twice — never overuse.
- "Most members spend $2K-$5K a year on travel — more or less for you?"
  is a great rhythm. Open-ended, conversational, makes them think.
- Anchor urgency on the Quarterly Special (next window typically May
  12-17, then quarterly). Frame it as "the Black Friday of travel."
- After each benefit, do a TRIAL CLOSE that ties to discovery.
- Don't push when they say "not right now." Leave the door open: "your
  credits stay in your account whenever you're ready."

# The 4 benefit pillars (memorize the cheat sheet)

Have the member write 1 through 4 on a piece of paper. Use this exact
brochure framing:
  1. Savings Credits = Deep Discounts
  2. Reward Points = Free Vacations
  3. Great Getaways = $499 Resort Weeks
  4. Quarterly Specials = $7/day Resorts & $50/day Cruises

"What takes 10 minutes to explain I can show you in 5 — pull up your
account at govvacationrewards.com if you can, and we'll walk through
it together."

## 1. Savings Credits — "Deep Discounts"
Member-only currency that buys retail prices down to wholesale on
hotels, resorts, cruises, and tours. Most members save 15-50% per
booking. NOT cash. NOT a gift card. Applied at checkout.

Concrete examples (ONLY use these — they're approved):
  - "5-star hotel in Destin: $1,200 retail → $658 with credits.
    $525 saved."
  - "Royal Caribbean cruise: $2,000 retail → $1,100 with credits.
    $900 saved."

Trial close: "Where do you think you'd use those credits first —
based on what you told me about [discovery detail]?"

## 2. Reward Points — "Free Vacations"
Earn 5 points per dollar spent on travel. Three ways to earn:
  1) Every dollar YOU spend on travel = 5 points
  2) Anything friends/family book through your account = points to YOU
  3) Referrals — anyone you refer who becomes a Select Access member =
     50,000 points to YOU (a free cruise or resort week)

Redemptions:
  - Resort stays: starting at 25,000 points
  - All-inclusive resorts: starting at 35,000 points
  - 3-7 day cruises: 50,000 points
  - Premium resort weeks / 7-day cruises: 75,000-100,000 points

Concrete examples:
  - "A member redeemed 35,000 points for a Cancun all-inclusive that
    normally costs $2,898 — totally free."
  - "Another redeemed 50,000 points for a Carnival Celebration
    cruise worth $1,788 — totally free."

Trial close: "If you had 50,000 points right now, where would you go
first?"

## 3. Great Getaways — "$499 Resort Weeks"
Last-minute resort deals on 150,000+ hotels, 30-90 days out. Members
NEVER pay more than $499 for the entire week (NOT per night, NOT per
person — the whole stay). 3, 5, and 7-night options. Already priced
below cost. Friends and family extension available.

Concrete example:
  - "A member booked The Venetian in Vegas — normally $2,137 — for
    $299 for the whole week. That's cheaper than Starbucks per night."

Trial close: "If you found a Great Getaway to [discovery destination],
which one would you book first?"

## 4. Quarterly Specials — "$7/day Resorts & $50/day Cruises"
"Black Friday of travel" — 4 windows per year (typically Feb, May,
Aug, Nov). Up to 50-90% off. Resort stays as low as $7/day, cruises
as low as $50/day. ONE booking per quarter per member. Booking window
is the limited part — actual travel can be later. Next window is
typically the next month.

Concrete example:
  - "A member booked Waikiki Beach — normally $2,500 — during a
    Quarterly Special for $1,250."

Trial close: "If you could see a resort or cruise at 50% off right
on your screen during the next window, where would you want to go?"

# Conversation flow (7 stages — pick what fits)

## STAGE 1 — Greeting (already handled by the opener)

After they respond, ROUTE (DEFAULT IS LIVE TRANSFER NOW):
- "yes connect me / sounds good / sure" → STAGE 6 (transfer NOW)
- "tell me about my benefits first / explain it" → STAGE 2 (education
  walkthrough, then back to STAGE 4 transfer offer)
- "I can't talk now / busy / call me later" → STAGE 5 (text the
  Microsoft Bookings link as fallback)
- "wrong number / not me" → end gracefully (wrong_person)
- "remove me / DNC / stop calling" → end gracefully (dnc)

## STAGE 2 — Discovery (2-3 conversational questions)

"Before I walk you through everything, let me ask a couple quick
travel questions — it'll make this a lot more useful for you."

Pick 2-3 of these — match the energy of the conversation:
  - "When you signed up, where were you looking to travel?"
  - "Give me your top 3 destinations — domestic or international?"
  - "When was the last time you went on a cruise?"
  - "What's a cruise you've wanted to do but haven't yet?"
  - "When did you last stay at an all-inclusive? Would you do
    Mexico or DR?"
  - "Most members spend $2K-$5K a year on travel — more or less for
    you?"
  - "Anyone in your family who travels like you do?"
  - "Sounds like you book most of the travel — yeah?"

After 2-3 answers, REFLECT back:
  "I love that. So just to make sure I'm on the same page —
  sounds like you're really into [2-3 specifics from their answers].
  That's perfect, because what I'm about to show you was built for
  travelers like you."

## STAGE 3 — Benefits Education (the 4 pillars)

Walk through 1, 2, 3, 4 in order using the cheat-sheet structure.
After EACH benefit, run the trial close that ties to their discovery
answers. If they're at a screen, walk them through the website live.

"Pull up your account at govvacationrewards.com, click 'My Benefits'
— write 1 through 4 on a piece of paper. What I can tell you in ten
minutes I can show you in five."

Don't lecture — converse. Pause for reactions.

## STAGE 4 — Call to Action

PRIMARY: Warm transfer + bonus carrot
  "Here's something I can do for you right now. We have a travel
  specialist standing by — and if I connect you today, they'll load
  another {{transfer_bonus_amount}} in cash credits directly into
  your account. So you'll have {{total_after_bonus}} total. Want me
  to connect you?"

If yes → call `transfer_to_specialist` with a private brief.
If hesitant → run the OBJECTION LOOP:
  1. Temperature check: "Does what I walked you through make sense?
     1-10, how would you rate what you've seen?"
  2. Tie to discovery: "You mentioned [discovery detail] — I want to
     make sure you actually get there. That's worth a few minutes,
     right?"
  3. Make the offer real: "{{transfer_bonus_amount}} goes straight
     into your account. No commitment to anything else."
  4. Ask again: "Can I go ahead and connect you?"

If still no after one rebuttal → SECONDARY (link).

SECONDARY: Microsoft Bookings link (Teams meeting later)
  "No problem at all. I can text or email you a link to book a time
  with one of our travel specialists — whatever works best for your
  schedule. The appointment is free, takes 15-20 minutes, and when
  you show up, they'll load {{transfer_bonus_amount}} in cash
  credits into your account just for being there."

  Then: "Do you want that as a text or an email?" Wait. Confirm the
  destination ("just to be sure, that's [repeat]?"). Then call
  `send_scheduler_link(channel='sms'|'email', destination='...')`.
  Confirm receipt: "Just sent — should be there in the next minute."

## STAGE 5 — Member wants link only (skip benefit walk)

"Absolutely. Quick heads-up though — when you connect with the
specialist, they'll load {{transfer_bonus_amount}} in cash credits
into your account during the call. Worth the 15 minutes. What's the
best number for the text?"

Then call `send_scheduler_link`.

## STAGE 6 — Member wants live person now

"Absolutely — let me get a specialist on the line right now. They
can answer any questions, walk through your account, and load
{{transfer_bonus_amount}} in cash credits today. One moment."

Then call `transfer_to_specialist` immediately.

## STAGE 7 — Call close (pick ONE that matches the call's energy)

  - Warm/personal (they opened up): "It was so nice chatting today!
    Travel really does make life richer — and you've got great
    benefits waiting. Hope your next trip is everything you've been
    dreaming about. Take care!"
  - Energetic (they got excited): "You're all set! Honestly a little
    jealous — you've got serious travel perks now. Go plan something
    amazing. Safe travels!"
  - Confident (skeptic-turned-convert): "You came in with questions,
    you're leaving with answers — love that. Don't let those benefits
    sit too long. Have a wonderful day."
  - Soft/reassuring (hesitant or just took the link): "Whether you
    book next month or just start dreaming — your benefits are there
    whenever you're ready. Wishing you safe travels."
  - Brief (short call, link only): "Thanks for your time — pleasure.
    Account's ready when you are. Have a great day!"

Then call `hangup_call(reason="...")`.

## STAGE 8 — Not engaged (busy / can't talk now — outbound)

"No problem — totally get it. Couple options: I can text or email
you a link to schedule when it works better, OR connect you to a
specialist now if you've got 60 seconds, OR we leave it for now and
the credits stay in your account. Which works?"

- "send me the link" → STAGE 5
- "connect me now" → STAGE 6
- "leave it" → graceful end with "your credits stay in your account
  whenever you're ready"

# Rebuttals (from GVR Call Transfer Script)

If the caller says...
  - "I'm busy" → "No problem, won't take much time — and you'll
    love this. These are the benefits you earned. Just a couple
    quick travel questions."
  - "Not interested" → "Don't worry — calling about a membership
    you already have. Just want to make sure you know the best way
    to use it. Couple of travel questions, that's all."
  - "Call me back" → "Of course — but there's a couple of special
    promos that can only run today. Let me grab the specialist real
    quick."
  - "My spouse handles that" → "Sure — they trust your judgment.
    Let me grab the specialist to explain — you can relay. Fair?"
  - "Not traveling right now" → "Totally fair. We can both agree
    you'll travel over the next few years, right? Let me get the
    specialist on briefly. If you like what you hear, great — if
    not, at least you know how to use what you have."
  - "I don't have time" → "I promise this won't take long. Couple
    things on your account I really need you to know about. Let me
    get you over to your specialist."

# Tools

- `verify_me_to_caller()` — Returns a structured verification offer
  (masked email/phone on file, callback options, explicit
  "never-ask-for" list). USE THIS the FIRST time the caller seems
  wary, asks "is this a scam," "how did you get my number," or "how
  do I know you're real." Per FTC + USAA + Marriott consumer
  guidance: the safest trust move is helping the caller verify YOU
  on their terms. Don't argue trust — invite verification.
- `lookup_objection(objection_text)` — 84-entry rebuttal library
  across 10 categories (skepticism/trust, time, travel-fit,
  cost/value, privacy, negative-past, decision-maker, channel,
  life-stage, rejection). USE THIS the FIRST time the caller
  raises any objection beyond pure trust suspicion. Don't improvise.
- `lookup_faq(question_text)` — Canonical GVR FAQ answers
  (51 entries). USE THIS the FIRST time a caller asks any factual
  question you're not 100% certain about. If no_match: "great
  question — the specialist can confirm that for you" → STAGE 4 or 6.
- `send_scheduler_link(channel, destination, caller_name)` — Texts
  or emails the Microsoft Bookings scheduler URL. Use after the
  member declines transfer but agrees to schedule.
- `transfer_to_specialist(reason, brief)` — Warm-transfer the SIP
  participant to the live specialist. ALWAYS pass a brief privately
  before bridging — caller name, what they want, credit balance, any
  objection you handled. End brief with "Ready to bridge?".
- `hangup_call(reason)` — End the call cleanly after STAGE 7.

# Goal

Every call ends in ONE of these outcomes — never in confusion:
  1. Warm transfer to specialist (BEST — caller earns
     {{transfer_bonus_amount}} bonus, total {{total_after_bonus}}
     on their account)
  2. Microsoft Bookings link sent + receipt confirmed
  3. Polite goodbye with credits left in their account
  4. DNC honored if requested
""".strip()


def render_persona(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_MEMBER_CONTEXT, **(ctx or {})}
    return PERSONA_INSTRUCTIONS_TEMPLATE.format(**merged)


def render_greeting(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_MEMBER_CONTEXT, **(ctx or {})}
    direction = merged.get("direction", "inbound")
    template = (
        GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE
        if direction == "outbound"
        else GREETING_INSTRUCTIONS_INBOUND_TEMPLATE
    )
    return template.format(**merged)


def parse_metadata(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("metadata is not valid JSON")
        return {}
    return {k: str(v) for k, v in data.items() if v is not None}


class AndieAgent(Agent):
    def __init__(self, member_context: dict[str, str] | None = None) -> None:
        merged_ctx = {**DEFAULT_MEMBER_CONTEXT, **(member_context or {})}
        # super() MUST come first — Agent base class sets self._id
        # there. Custom attrs after.
        super().__init__(instructions=render_persona(merged_ctx))
        self._member_context = merged_ctx

    @function_tool(
        name="lookup_faq",
        description=(
            "Look up the canonical GVR answer to a member's factual "
            "question. Use whenever a caller asks about benefits, "
            "Savings Credits, Reward Points, Quarterly Specials, "
            "Great Getaways, account details, eligibility, redemption, "
            "blackout dates, login, etc. Speak the answer naturally. "
            "If no_match: 'great question — the specialist can confirm "
            "that for you' and route to STAGE 4 or 6."
        ),
    )
    async def lookup_faq(self, question_text: str) -> dict:
        matches = match_qa(question_text)
        if not matches:
            return {
                "no_match": True,
                "guidance": (
                    "Acknowledge: 'Great question — the specialist can "
                    "give you the exact details when I connect you.' "
                    "Then offer transfer (STAGE 4) or scheduler "
                    "(STAGE 4B)."
                ),
            }
        m = matches[0]
        return {
            "no_match": False,
            "matched_question": m.question,
            "answer": m.answer,
            "score": round(m.score, 3),
            "instruction": (
                "Speak the answer naturally — don't read it stiffly. "
                "Then trial-close back into the flow."
            ),
        }

    @function_tool(
        name="verify_me_to_caller",
        description=(
            "Use this when the caller seems wary, suspicious, or asks "
            "any variant of 'is this a scam' / 'how do I know you're "
            "real' / 'how did you get my number'. Returns a structured "
            "verification you can offer THE CALLER — partial email or "
            "phone on file, official callback URL, and an explicit "
            "list of what you will NEVER ask for. Per FTC/Marriott/USAA "
            "consumer-protection guidance: the safest trust move is "
            "always to help the caller verify YOU on their terms."
        ),
    )
    async def verify_me_to_caller(self) -> dict:
        member = self._member_context
        # Mask the email for read-back
        email = member.get("enrollment_email", "")
        masked_email = ""
        if email and "@" in email:
            local, domain = email.split("@", 1)
            masked_email = f"{local[:2]}***@{domain}"
        masked_phone = ""
        phone = member.get("caller_phone") or member.get("phone_number") or ""
        if phone and len(phone) >= 4:
            masked_phone = f"***-***-{phone[-4:]}"

        return {
            "instruction": (
                "Offer the caller these verification options in your "
                "own warm voice — DON'T read this back word-for-word. "
                "Pick the 1-2 that fit the caller's mood best. End by "
                "letting them choose:"
            ),
            "verification_options": [
                f"Confirm the email on file (last digits): {masked_email}"
                if masked_email
                else "Confirm the email on file with the caller",
                f"Confirm the phone on file: {masked_phone}"
                if masked_phone
                else "Confirm the phone on file with the caller",
                f"Confirm the enrollment date: {member.get('enrollment_date', '<not on file>')}",
                "Hang up and call back the number on the back of their "
                "membership card or on govvacationrewards.com",
                "Log into govvacationrewards.com directly and find the "
                "credits under 'My Benefits'",
                "Receive a one-time passcode at the registered email "
                "to verify the call is real",
            ],
            "never_ask_for": [
                "Credit card number, CVV, or expiration date",
                "Bank account or routing numbers",
                "Social Security Number, full DOB, driver's license",
                "One-time passwords, login codes, or SMS codes",
                "Login passwords or security-question answers",
                "Wire transfers, gift cards, or cryptocurrency",
            ],
            "caller_facing_summary": (
                "I can verify myself to you — happy to confirm the "
                "last few characters of the email on file, the "
                "enrollment date, or you can hang up and call us "
                "directly from the number on your membership card or "
                "from govvacationrewards.com. And just so you know — "
                "I'll never ask for your credit card, social security "
                "number, login password, or a one-time code on this "
                "call. What works best for you?"
            ),
        }

    @function_tool(
        name="lookup_objection",
        description=(
            "Look up the canonical rebuttal for a member's objection "
            "across 10 categories: skepticism/trust, time pressure, "
            "travel fit, cost/value, privacy/data, negative past "
            "experience, decision-maker authority, channel preference, "
            "life stage, and outright rejection. USE THIS the FIRST "
            "time the member raises any objection — don't improvise. "
            "Speak the rebuttal in your own warm voice. If no_match, "
            "acknowledge briefly and offer to text the link or "
            "transfer."
        ),
    )
    async def lookup_objection(self, objection_text: str) -> dict:
        """Pull the best-matching rebuttal from the 84-entry library."""
        matches = match_objection(objection_text)
        if not matches:
            return {
                "no_match": True,
                "guidance": (
                    "Acknowledge warmly in one short line, then offer "
                    "two options: 'I can text you a link to schedule "
                    "later, or I can connect you to a specialist now "
                    "— which works?' Don't keep arguing."
                ),
            }
        m = matches[0]
        return {
            "no_match": False,
            "category": m.category,
            "matched_objection": m.objection,
            "rebuttal": m.rebuttal,
            "score": round(m.score, 3),
            "instruction": (
                "Speak the rebuttal naturally — don't read it word "
                "for word if it sounds stilted. Then immediately "
                "offer the next step (transfer or scheduler link)."
            ),
        }

    @function_tool(
        name="send_scheduler_link",
        description=(
            "Send the Microsoft Bookings scheduling link via SMS or "
            "email. Use when the member declines a live transfer but "
            "agrees to schedule a callback. ALWAYS confirm the "
            "destination by repeating it back BEFORE sending."
        ),
    )
    async def send_scheduler_link(
        self,
        channel: str,
        destination: str,
        caller_name: str = "",
    ) -> dict:
        """Calls the existing arrivia-gvr endpoint that sends the link.

        Args:
            channel: 'sms' or 'email'
            destination: E.164 phone for sms, email address for email
            caller_name: Member's first name if captured
        """
        import httpx

        url = "https://arrivia-gvr.vercel.app/api/tools/send-scheduler-link"
        api_key = os.environ.get("ARRIVIA_GVR_API_KEY", "")
        if not api_key:
            logger.warning("send_scheduler_link: ARRIVIA_GVR_API_KEY not set")
            return {"success": False, "error": "api_key_not_configured"}
        payload = {
            "channel": channel,
            "destination": destination,
            "caller_name": caller_name or self._member_context.get("member_name", ""),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    json=payload,
                    headers={"x-api-key": api_key},
                )
            if r.status_code >= 400:
                logger.warning(
                    "send_scheduler_link failed: %s %s",
                    r.status_code,
                    r.text[:200],
                )
                return {"success": False, "error": f"http_{r.status_code}"}
            logger.info("scheduler link sent via %s to %s", channel, destination)
            return {"success": True, "channel": channel, "destination": destination}
        except Exception as e:
            logger.warning("send_scheduler_link exception: %s", e)
            return {"success": False, "error": str(e)}

    @function_tool(
        name="transfer_to_specialist",
        description=(
            "Warm-transfer the member to a live GVR travel specialist. "
            "Use after they accept the transfer offer in STAGE 4 or "
            "STAGE 6. Pass a brief — the specialist uses it to pick up "
            "where you left off."
        ),
    )
    async def transfer_to_specialist(self, reason: str, brief: str = "") -> dict:
        """Warm-transfer via DIAL-AND-BRIDGE (not SIP REFER).

        We dial the specialist via the LiveKit outbound trunk and bring
        them INTO the same room as the caller. Caller never leaves
        the room — they stay connected throughout the dial-and-pickup
        window, and the moment the specialist answers, all three
        participants (caller, specialist, Andie) are in the same audio
        bridge.

        Why not REFER (TransferSipParticipant)?
          - REFER tells the carrier to redirect the SIP call. The
            caller leaves the LiveKit room entirely; PSTN handles the
            new bridge. We lose recording, transcript, observability.
          - REFER is also UNSUPPORTED on LiveKit Phone Numbers
            (which is what +16892608790 is). Method B works on both
            LK numbers and Twilio trunks — single code path.

        Sequence:
          1. Verify the inbound caller is still in the room (SIP
             participant present).
          2. Dial the specialist via the configured outbound trunk
             (`LIVEKIT_SIP_OUTBOUND_TRUNK_ID`). Adds a new SIP
             participant on connect.
          3. Wait until the specialist actually picks up (or returns
             a SIP busy / no-answer error).
          4. Speak a one-line handoff to brief the specialist with
             the caller listening. Then close Andie's session so the
             humans can talk freely.

        On failure (busy, no-answer, no outbound trunk configured),
        return error so the LLM can apologize and offer the scheduler
        link as a fallback.
        """
        ctx = agents.get_job_context()
        if ctx is None:
            return {"transferred": False, "error": "no_job_context"}

        target = os.environ.get(
            "SPECIALIST_PHONE",
            self._member_context.get("specialist_phone", ""),
        )
        if not target or target.startswith("+1555") or target == "+10000000000":
            logger.warning("transfer_to_specialist: SPECIALIST_PHONE missing/placeholder")
            return {"transferred": False, "error": "specialist_phone_not_configured"}

        outbound_trunk = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        if not outbound_trunk:
            return {"transferred": False, "error": "outbound_trunk_not_configured"}

        # Caller must still be on the line.
        sip_p = next(
            (
                p for p in ctx.room.remote_participants.values()
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
            ),
            None,
        )
        if sip_p is None:
            return {"transferred": False, "error": "no_sip_participant"}

        # The number Andie shows the specialist as the caller-ID. Use
        # Andie's own line if set (LK Phone Number or Twilio); fall
        # back to TWILIO_VOICE_NUMBER for Deedy-shared trunks.
        caller_id = (
            os.environ.get("LIVEKIT_PHONE_NUMBER")
            or os.environ.get("TWILIO_VOICE_NUMBER")
            or "+14072890294"
        )

        specialist_identity = f"specialist-{target.lstrip('+')}"

        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=outbound_trunk,
                    sip_call_to=target,
                    sip_number=caller_id,
                    participant_identity=specialist_identity,
                    participant_name="GVR Specialist",
                    krisp_enabled=True,
                    # Block until the specialist actually picks up.
                    # If they don't, we get a TwirpError below and
                    # can fall back to the scheduler-link offer.
                    wait_until_answered=True,
                )
            )
        except api.TwirpError as e:
            sip_code = e.metadata.get("sip_status_code") if e.metadata else None
            logger.warning(
                "specialist_dial_failed reason=%s sip=%s msg=%s",
                reason, sip_code, e.message,
            )
            return {
                "transferred": False,
                "error": "specialist_unavailable",
                "sip_status_code": sip_code,
            }
        except Exception as e:
            logger.warning("transfer_to_specialist unexpected: %s", e)
            return {"transferred": False, "error": str(e)}

        logger.info(
            "specialist_bridged target=%s reason=%s brief=%r room=%s",
            target, reason, brief[:120], ctx.room.name,
        )

        # One-line briefing the specialist hears with the caller still
        # on the line. Keeps it warm — caller hears Andie hand them off
        # to a real human by name/context, not a cold drop-off.
        try:
            session = self.session  # type: ignore[attr-defined]
            member_name = self._member_context.get("member_name", "this member")
            handoff_line = (
                f"Hi, this is Andee — connecting you with {member_name}. "
                f"Quick brief: {_truncate_at_word(brief, 200) if brief else reason}. "
                f"I'll let you two take it from here."
            )
            await session.say(handoff_line, allow_interruptions=False)
            # Close the agent's session so the humans can talk without
            # Andee re-engaging. The room stays open for caller +
            # specialist; LiveKit closes it when both leave.
            await session.aclose()
        except Exception as e:
            # Non-fatal — the bridge is up even if the goodbye line
            # didn't make it. Log and return success.
            logger.warning("handoff_line failed (bridge still up): %s", e)

        return {"transferred": True, "target": target, "method": "dial_and_bridge"}

    @function_tool(
        name="hangup_call",
        description=(
            "End the call cleanly after STAGE 7. Reason tags the exit "
            "for analytics — use one of: transferred, "
            "scheduled_callback, not_interested, dnc, wrong_person, "
            "completed_no_action."
        ),
    )
    async def hangup_call(self, reason: str = "completed") -> dict:
        try:
            ctx = agents.get_job_context()
            if ctx is None:
                return {"ended": False, "error": "no_job_context"}
            await ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=ctx.room.name)
            )
            logger.info("hangup_call: reason=%s", reason)
            return {"ended": True, "reason": reason}
        except Exception as e:
            logger.warning("hangup_call exception: %s", e)
            return {"ended": False, "error": str(e)}


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    member_ctx = parse_metadata(ctx.job.metadata)
    if not member_ctx and ctx.room.metadata:
        member_ctx = parse_metadata(ctx.room.metadata)

    phone_number = member_ctx.get("phone_number")
    if phone_number and "direction" not in member_ctx:
        member_ctx["direction"] = "outbound"
    elif "direction" not in member_ctx:
        member_ctx["direction"] = "inbound"

    direction = member_ctx["direction"]
    logger.info(
        "joining room=%s direction=%s phone=%s member=%s",
        ctx.room.name,
        direction,
        phone_number or "<inbound>",
        member_ctx.get("member_name", "there"),
    )

    sip_participant = None
    if direction == "outbound" and phone_number:
        trunk_id = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        if not trunk_id:
            logger.error("LIVEKIT_SIP_OUTBOUND_TRUNK_ID missing — cannot dial")
            ctx.shutdown()
            return
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    sip_number=os.environ.get("TWILIO_VOICE_NUMBER", "+14072890294"),
                    participant_identity=phone_number,
                    participant_name=member_ctx.get("member_name", "Member"),
                    krisp_enabled=True,
                    wait_until_answered=True,
                )
            )
        except api.TwirpError as e:
            sip_code = e.metadata.get("sip_status_code") if e.metadata else None
            logger.warning("outbound did not connect: %s (SIP %s)", e.message, sip_code)
            ctx.shutdown()
            return
        sip_participant = await ctx.wait_for_participant(identity=phone_number)
    else:
        try:
            sip_participant = await ctx.wait_for_participant()
        except Exception as e:
            logger.warning("no participant arrived: %s", e)
            ctx.shutdown()
            return

    if sip_participant:
        attrs = getattr(sip_participant, "attributes", {}) or {}
        sip_phone = attrs.get("sip.phoneNumber") or attrs.get("sip.from")
        if sip_phone and "phone_number" not in member_ctx:
            member_ctx["phone_number"] = sip_phone
            logger.info("inbound caller phone: %s", sip_phone)

    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p):  # type: ignore[no-untyped-def]
        if sip_participant and p.identity != sip_participant.identity:
            return
        reason = getattr(p, "disconnect_reason", None)
        logger.info("caller disconnected reason=%s", reason)

    # --- INFRA FAILOVER (LiveKit FallbackAdapter) ---------------------
    # Mirror Deedy: provider failover for STT/LLM/TTS so a regional
    # outage at one provider doesn't drop the call mid-conversation.
    from livekit.agents.llm import FallbackAdapter as LLMFallback
    from livekit.agents.stt import FallbackAdapter as STTFallback
    from livekit.agents.tts import FallbackAdapter as TTSFallback

    primary_stt = inference.STT(
        model="deepgram/flux-general",
        language="en",
        extra_kwargs={
            "eager_eot_threshold": 0.7,
            "eot_threshold": 0.9,
            "eot_timeout_ms": 2000,
        },
    )
    fallback_stt = inference.STT(model="deepgram/nova-3", language="en")

    primary_llm = inference.LLM(
        model="xai/grok-4.20-0309-non-reasoning",
        extra_kwargs={
            "temperature": 0.0,
            "max_completion_tokens": 400,
            "parallel_tool_calls": False,
        },
    )
    fallback_llm_grok = inference.LLM(
        model="xai/grok-4-1-fast-non-reasoning",
        extra_kwargs={
            "temperature": 0.0,
            "max_completion_tokens": 400,
            "parallel_tool_calls": False,
        },
    )
    fallback_llm_openai = inference.LLM(
        model="openai/gpt-4.1-mini",
        extra_kwargs={"temperature": 0.0, "max_completion_tokens": 400},
    )

    primary_tts = inference.TTS(
        # Cove voice on Rime mistv3 — distinct from Deedy's Lagoon.
        model="rime/mistv3",
        voice="cove",
        language="en",
    )
    fallback_tts_arcana = inference.TTS(
        model="rime/arcana", voice="luna", language="en",
    )
    fallback_tts_cartesia = inference.TTS(
        model="cartesia/sonic-2", voice="warm-female", language="en",
    )

    session = AgentSession(
        stt=STTFallback(
            [primary_stt, fallback_stt],
            attempt_timeout=3.5,
            max_retry_per_stt=0,
            retry_interval=0.5,
        ),
        llm=LLMFallback(
            [primary_llm, fallback_llm_grok, fallback_llm_openai],
            attempt_timeout=5.0,
            max_retry_per_llm=0,
            retry_interval=0.5,
        ),
        tts=TTSFallback(
            [primary_tts, fallback_tts_arcana, fallback_tts_cartesia],
            attempt_timeout=4.0,
            max_retry_per_tts=1,
        ),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detection="stt"),
        ivr_detection=True,
        allow_interruptions=True,
        min_interruption_words=2,
        min_interruption_duration=0.4,
    )

    # Per-call usage telemetry — logs + dashboard POST.
    @session.on("session_usage_updated")
    def _on_usage(ev) -> None:  # type: ignore[no-untyped-def]
        u = getattr(ev, "usage", ev)
        payload = {
            "llm_prompt_tokens": getattr(u, "llm_prompt_tokens", None),
            "llm_completion_tokens": getattr(u, "llm_completion_tokens", None),
            "tts_characters": getattr(u, "tts_characters_count", None),
            "stt_audio_seconds": getattr(u, "stt_audio_duration", None),
        }
        logger.info("usage room=%s %s", ctx.room.name, payload)
        _fire_telemetry(ctx.room.name, "usage_update", payload)

    async def _on_shutdown() -> None:
        reason = str(getattr(ctx, "shutdown_reason", "unknown"))
        logger.info("shutdown room=%s reason=%s", ctx.room.name, reason)
        try:
            await _generate_call_summary(session, ctx.room.name, member_ctx)
        except Exception:
            pass
        try:
            await _post_agent_event(
                ctx.room.name, "shutdown", {"shutdown_reason": reason}
            )
        except Exception:
            pass

    ctx.add_shutdown_callback(_on_shutdown)

    # Recording is fire-and-forget — never blocks session.start().
    _start_room_recording_in_background(ctx)

    # Pin input to the SIP caller (see Deedy worker for full rationale).
    room_input = RoomInputOptions(
        noise_cancellation=noise_cancellation.BVCTelephony(),
    )
    if sip_participant is not None:
        room_input = RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            participant_identity=sip_participant.identity,
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
        )

    await session.start(
        agent=AndieAgent(member_context=member_ctx),
        room=ctx.room,
        room_input_options=room_input,
    )

    await session.generate_reply(instructions=render_greeting(member_ctx))


def cli_main() -> None:
    """Console entrypoint exposed as `andie-worker`.

    Explicit dispatch (`agent_name="andie-gvr"`). The dispatch rule
    bound to LiveKit Phone Number +16892608790 must include
    roomConfig.agents = [{ agentName: "andie-gvr" }] so this worker
    receives those calls (and Deedy does NOT).
    """
    agents.cli.run_app(
        WorkerOptions(entrypoint_fnc=entrypoint, agent_name="andie-gvr")
    )


cli = cli_main


if __name__ == "__main__":
    cli_main()
