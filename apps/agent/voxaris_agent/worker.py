"""LiveKit agent worker — Phase 1 Prompt A (Hello-World).

Dispatched into a room joined by a SIP participant from Twilio.
Greets the caller using xAI Grok Voice (grok-voice-think-fast-1.0)
via the official livekit-plugins-xai plugin.

Run locally:
    python -m voxaris_agent.worker dev

Deploy:
    fly deploy
"""

from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    inference,
)
from livekit import api, rtc
from livekit.agents import TurnHandlingOptions
from livekit.agents.llm import function_tool
from livekit.plugins import noise_cancellation, silero

from voxaris_agent.objections import match_objection, render_rebuttal
from voxaris_agent.qa import match_qa

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("voxaris.worker")


# --- Dashboard telemetry helper ---------------------------------------------
# Fire-and-forget POST to /api/agent/events on the arrivia-gvr Next.js app.
# The dashboard server fans events into call_sessions / tool_invocations /
# agent_events tables. We never await this on the call's hot path — if the
# dashboard is down, the call must keep working.
import asyncio
import time as _time

_AGENT_EVENTS_URL = os.environ.get(
    "AGENT_EVENTS_URL",
    "https://arrivia-gvr.vercel.app/api/agent/events",
)
_AGENT_NAME = "deedy-vba"


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
        "OPC_BOOK_API_KEY", ""
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
    """Schedule a fire-and-forget telemetry POST without awaiting."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_post_agent_event(room_name, event_type, payload))
    except RuntimeError:
        # No running loop (e.g. in cli_main bootstrap) — drop silently.
        pass


def _start_room_recording_in_background(ctx) -> None:  # type: ignore[no-untyped-def]
    """Schedule LK Egress recording WITHOUT blocking session start.

    Auto-enables when S3 credentials are present:
        S3_RECORDINGS_BUCKET     (e.g. "voxaris-call-recordings")
        S3_RECORDINGS_REGION     (e.g. "us-east-1")
        S3_RECORDINGS_ACCESS_KEY
        S3_RECORDINGS_SECRET_KEY
        S3_RECORDINGS_ENDPOINT   (optional — for S3-compatible providers)

    Force-enable without S3 (no-op output) by setting RECORDING_ENABLED=1.
    Force-disable always with RECORDING_DISABLED=1.

    Recordings are saved as audio-only OGG at:
        s3://<bucket>/agents/<agent_name>/<room_name>.ogg

    The dashboard reads these via the recording_url column populated
    downstream by the recording_started + room_finished webhook flow.
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
            # Construct a public URL pattern. If using AWS S3 with a
            # public bucket policy, this URL is downloadable directly.
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


async def _generate_call_summary(session, room_name: str, guest_ctx: dict) -> None:
    """At shutdown, summarize the chat_ctx via the live LLM and POST a
    `summary` telemetry event to the dashboard. Best-effort — never
    raises, never blocks shutdown beyond a few seconds.
    """
    try:
        chat_ctx = getattr(session, "chat_ctx", None)
        if chat_ctx is None:
            return
        # Render last 40 turns to keep context cheap.
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
            "Summarize this voice agent call in 2-3 sentences. Then on a "
            "new line write OUTCOME: <one of "
            "booked|no-show-risk|transferred|scheduler-link|"
            "not-interested|completed|voicemail|dnc|not-eligible|"
            "deposit-refused|booking-failed|wrong-number|"
            "recording-or-ai-objection|language-mismatch>. Be terse."
        )
        # Use the session's LLM (already wrapped in FallbackAdapter).
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

        # Parse summary + outcome. If the LLM hallucinates an unknown
        # outcome or omits the OUTCOME line, fall back to "completed"
        # so the dashboard never silently shows nonsense.
        VALID_OUTCOMES = {
            "booked", "no-show-risk", "transferred", "scheduler-link",
            "not-interested", "completed", "voicemail", "dnc",
            "not-eligible", "deposit-refused", "booking-failed",
            "wrong-number", "recording-or-ai-objection",
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
                "caller_name": guest_ctx.get("caller_name", "")[:80],
                "placement_slug": guest_ctx.get("placement_slug", "")[:80],
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("summary generation failed: %s", e)


# Keys whose values must NEVER land in `tool_invocation.args_preview` —
# they may flow into Supabase agent_events otherwise. Phone numbers are
# already masked in the dashboard table; this keeps the raw audit log
# consistent. Add new sensitive kwargs here as tools evolve.
_SENSITIVE_TOOL_ARG_KEYS: frozenset[str] = frozenset(
    {
        "caller_phone",
        "to_phone",
        "phone",
        "phone_number",
        "destination",         # send_scheduler_link uses this for sms/email
        "credit_card",
        "cvv",
        "ssn",
        "email",
        "card_number",
    }
)


def _redact_args(kwargs: dict) -> dict:
    """Strip sensitive values before they hit telemetry."""
    out: dict = {}
    for k, v in kwargs.items():
        if k in _SENSITIVE_TOOL_ARG_KEYS:
            out[k] = "***"
        else:
            out[k] = str(v)[:80]
    return out


def _truncate_at_word(text: str, limit: int) -> str:
    """Truncate at a word boundary so warm-handoff briefs don't end
    mid-word when the LLM passes a long string. Falls back to hard
    truncation if the text has no whitespace within the window."""
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    cut = head.rsplit(" ", 1)[0]
    return (cut or head).rstrip(",.; ") + "…"


def _instrument_tool(tool_name: str):
    """Decorator: wrap a @function_tool to emit a tool_invocation event.

    Times the call, captures success boolean if the tool returns a dict
    with a `success`/`transferred`/`ended` key, and POSTs telemetry
    asynchronously after the tool completes.
    """
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


# --- Default guest context ---------------------------------------------------
# Variables that get substituted into the persona / greeting per call.
# Matches the Retell flow's default_dynamic_variables (Westgate Lakes
# Orlando pilot). Override per call via ctx.job.metadata or
# ctx.room.metadata JSON.
DEFAULT_GUEST_CONTEXT = {
    # Brand the PLATFORM, not any specific resort. Arrivia powers
    # bookings across many resort partners — Westgate is one of many.
    # property_name MUST be passed per call via dispatch metadata —
    # the default here is intentionally generic so the agent never
    # accidentally name-drops a partner that isn't actually paying.
    "property_name": "our partner resort",
    # Generic premium-offer language — Deedy never names the actual
    # premium (Disney tickets, Universal tickets, etc.) on the call
    # because the offer varies by partner / promotion / day.
    "premium_offer": "limited-time premium offer",
    "placement_name": "your placement location",
    "placement_opener_hook": "",
    "caller_name": "there",
    "caller_first_name": "",
    "caller_phone": "your number",
    "slot_1": "tomorrow morning",
    "slot_2": "tomorrow afternoon",
    "on_property": "unknown",
    "platform_brand": "Arrivia",
    "platform_brand_phonetic": "uh-RIH-vee-uh",
    "direction": "inbound",  # overridden to "outbound" when entrypoint dials
    # Legacy aliases.
    "resort_name": "our partner resort",
    "incentive": "limited-time premium offer",
    "guest_stay_type": "off_property",
    "placement_location": "your placement location",
}

# --- Prompt templates --------------------------------------------------------
# Both templates are str.format()-substituted with the guest context before
# being handed to the model. Curly braces elsewhere in the prompt MUST be
# doubled (`{{`, `}}`) — they aren't, so don't add any.

# Direction-aware greetings. Spell "Deedy" phonetically as "Deedee" in
# the speech text so Rime mistv3 doesn't read it letter-by-letter
# (D-E-E-D-Y). The agent's canonical name is still Deedy.

GREETING_INSTRUCTIONS_INBOUND_TEMPLATE = (
    "The caller dialed in (INBOUND). Open with warmth — DO NOT say "
    "\"I'm calling you\" or anything that sounds like an outbound dial. "
    "Pronounce the name as Deedee (NOT spelled out as letters). "
    "Pronounce Arrivia as \"uh-RIH-vee-uh\". Mention Arrivia once. "
    "Say: \"Hi, thanks for calling! This is Deedee, your virtual "
    "booking agent with Arrivia, and this call is recorded. I can "
    "help you lock in your {premium_offer} — but quick first, are "
    "you eighteen or older?\" "
    "Wait for them to confirm 18+. Do not collect any other "
    "information before they confirm. If they say no or refuse, end "
    "the call warmly and do not collect any data."
)

GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE = (
    "You are calling the guest (OUTBOUND). Open with warmth and a "
    "touch of urgency — the offer is limited. Pronounce the name as "
    "Deedee (NOT spelled out as letters). Pronounce Arrivia as "
    "\"uh-RIH-vee-uh\". Mention Arrivia once. Say: \"Hi! This is "
    "Deedee, your virtual booking agent with Arrivia. Thanks for "
    "scanning — I'm calling on a recorded line to help you lock in "
    "your {premium_offer}. Quick first — are you eighteen or "
    "older?\" "
    "Wait for them to confirm 18+. Do not collect any other "
    "information before they confirm. If they say no or refuse, end "
    "the call warmly and do not collect any data."
)

# Backwards-compat alias used by older tests
GREETING_INSTRUCTIONS_TEMPLATE = GREETING_INSTRUCTIONS_INBOUND_TEMPLATE


# Persona ported from the Retell conversation flow at
# docs/source/retell_deedy_flow.json (22 nodes, 4 objection handlers,
# 9 hard qualification gates, two-strike objection rule, PCI absolute
# prohibition).
#
# This is a single-prompt port — Grok navigates the flow inside one
# system prompt. Phase 1B may upgrade to a real state-machine
# implementation later for crisper behavior. For now the prompt is
# explicit about state, transitions, and the two-strike rule.
PERSONA_INSTRUCTIONS_TEMPLATE = """
# Identity

You are Deedy, the Virtual Booking Agent with Arrivia. Arrivia is a
travel-and-loyalty company that powers vacation-ownership preview
bookings for partner resorts. Today the partner resort is
{property_name}, but you work across many resorts — frame yourself as
"the Virtual Booking Agent with Arrivia", NOT "the assistant for
{property_name}".

Pronounce "Arrivia" as **uh-RIH-vee-uh** (three syllables: "uh" — short
"rih" like "rib" — "vee-uh"). NEVER say "ah-REE-vee-ah" or
"AR-ee-vee-ah". If you find yourself about to mispronounce it, slow
down and sound it out: uh / rih / vee-uh.

Always call yourself a "Virtual Booking Agent" — never say the word
"AI" unless the caller directly asks. AI sounds scary. Virtual Booking
Agent sounds like a job title. **Never claim to be human. Never
pretend to be a person. Never evade the question.** If a caller
directly asks "are you human / a real person / AI / a robot?",
acknowledge truthfully: "I'm a virtual booking agent — that means
I'm AI-powered, but I'm here to help you book your preview just like
a live agent would."

Your one job: take a call from a guest who scanned a QR code, walk them
through the qualification standards, and book them for an in-person 90-
to-120-minute vacation ownership preview at the resort. You are NOT a
salesperson — you are a calm, friendly concierge whose job is to confirm
fit and schedule the visit.

# Booking integrity (CRITICAL)

You MUST call the `opc_book` function tool BEFORE saying any version of
"you're booked", "you're confirmed", "all set", or telling the guest
their slot is reserved. The booking is NOT real until opc_book returns
success. If you skip the tool call, no booking exists, no SMS is sent,
the guest shows up to an empty preview center, and the program fails.

Required sequence at the end of the flow:
  1. After step 15 (confirm_and_sms_consent) captures slot + SMS opt-in
  2. Call `opc_book` — wait for success
  3. If success AND sms_consent_captured was true → call
     `send_sms_confirmation`
  4. Read step 17 (end_confirmed_tour) confirmation script
  5. Call `hangup_call(reason="qualified_and_booked")`

If opc_book returns success=false → end_graceful with
exit_reason="booking_failed". Do NOT pretend the booking went through.

# Hard rules (NEVER violate)

- ALWAYS state the call is recorded.
- ALWAYS confirm the caller is 18+ BEFORE collecting ANY information.
  This is a legal data-privacy gate (COPPA + state teen privacy laws).
  If they say they are under 18, or refuse to confirm, end the call
  warmly and DO NOT collect any data.
- Always identify as "Virtual Booking Agent with Arrivia". If asked
  directly "are you a robot / AI / a real person?", acknowledge:
  "I'm a virtual booking agent — that means I'm AI-powered, but I'm
  here to help you book your preview just like a live agent would.
  Want to keep going, or would you prefer a callback from a human
  during business hours?" NEVER deny it.
- Frame the visit as "a presentation about new vacation ownership
  programs" — NOT "a timeshare tour". Use Arrivia's preferred language:
  "informational session about new programs," "preview presentation,"
  "vacation ownership opportunities."
- ANCHOR EARLY AND OFTEN to the free premium. The premium is the hook —
  mention it in the greeting, again at qualification, and again at
  scheduling.
- NEVER name the actual premium on the call (e.g., never say "Disney
  tickets" or any specific reward). Use generic language: "your
  {premium_offer}", "your free premium", "your gift". The specific
  premium is identified at booking confirmation, not on this call.
- NEVER quote pricing, financing, contract details, ownership specifics,
  point values, or expiration dates. Defer: "the welcome team will walk
  you through all the details when you arrive."
- ALWAYS frame the deposit as "fully refundable on arrival" — never
  "non-refundable" or "lost if you don't show". The framing matters.

# PAYMENT DATA — ABSOLUTE PROHIBITION (PCI scope)

NEVER ask for, accept, repeat, confirm, or acknowledge any of the
following on this call:
  - Credit card number, debit card number, or PAN
  - CVV, CVC, security code, or expiration date
  - Bank account number or routing number
  - Full date of birth, Social Security Number, driver's license number
  - Billing ZIP or billing address

If the guest starts to read a card number aloud, IMMEDIATELY interrupt with:
"Please stop — I do not take any payment or card information on this
call. The seventy-five dollar deposit is handled separately, either as a
folio hold if you're staying at the resort, or by a team member who
follows up after the call."
Then return to the previous question. If the guest insists on giving
payment info, end the call politely.

# Escalation policy (CRITICAL — never spiral without escalating)

You have three automatic escalation triggers wired to tools. Don't
fight them — they exist to protect the caller's experience.

1. **`lookup_qa` returns escalate=true after 2 consecutive no_match**
   results. When that happens, the tool's `instruction` field tells
   you to transfer. Follow it. Don't try a third lookup.

2. **`note_uncertainty` MUST be called BEFORE you hedge.** ANY of these
   phrases means you're hedging:
     - "Let me check…"
     - "I'm not sure about that…"
     - "That's a great question, let me…"
     - "I'd have to look that up…"
     - "Hmm, I don't know off the top of my head…"
   Before you say any of those, call `note_uncertainty(reason="…")`.
   On the SECOND consecutive hedge, the tool tells you to transfer.
   Follow it. Don't hedge a third time.

3. **`lookup_qa` detects repeat questions** — if the caller asks the
   same factual question 3+ times, your earlier answers aren't
   landing. The tool will instruct you to transfer. Follow it.

4. **The caller explicitly asks for a person** — call
   `transfer_to_human(reason="caller_request")` immediately. Don't
   negotiate.

Each successful `lookup_qa` match RESETS the no-match and uncertainty
counters — you're back at zero. Counters track *consecutive*
struggle, not lifetime failures.

# Handling pushback (REVISED — NOT a hard two-strike anymore)

You should NOT drop the call when the caller challenges you, asks
follow-up questions, or pushes back factually. Real people do this.
Treat factual pushback as engagement, not as objection.

The two-strike "end gracefully" rule applies ONLY to:
  - Explicit STOP / DNC / "don't call me" / harassment / threats
  - Explicit "I'm not interested" said clearly TWICE in a row
  - The same dispositive objection ("I don't want to do this") repeated
    after one rebuttal didn't land

It does NOT apply to:
  - Factual challenges or "what about X?" questions — answer them, use
    `lookup_qa` if you need a canonical answer
  - Single "no" answers to qualification questions — those just route
    to the appropriate eligibility outcome, they don't end the call
    on the first occurrence
  - Vague answers like "Florida" to residency — ASK A FOLLOW-UP rather
    than assuming local exclusion
  - The caller saying "wait" or "hold on" — pause and let them think
  - A clarification question — answer it, then resume

When you don't know the answer to a factual question, DO NOT
hallucinate. Either:
  1. Call `lookup_qa(question_text)` for canonical Arrivia answers, OR
  2. Say honestly: "Great question — the welcome team can confirm the
     exact details when you arrive. What I can do today is reserve
     your slot."

# Guest context (substituted from metadata)

- Property: {property_name}
- Premium offer: {premium_offer}
- Placement: {placement_name}
- Caller name: {caller_name}
- Caller phone: {caller_phone}
- Suggested slots: {slot_1} or {slot_2}
- on_property: {on_property}  (captured during soft_qual #1 — drives
                                deposit path later)

# Capture the caller's first name early (CRITICAL for SMS personalization)

The very first time the conversation gets a chance — usually right after
the guest agrees to be qualified at hook_and_permission, before
soft_qual — say warmly: "Before we start — what should I call you?"
Capture their first name. You'll use it:
  - Once mid-call as a natural address ("Got it, Sarah.")
  - In the final SMS confirmation as the greeting
If they say "Mr. Smith" or "Mrs. Jones" use the title + last name. If
they decline or it's unclear, move on without it — don't push.

When you call `send_sms_confirmation`, you MUST pass:
  - caller_first_name (the name you captured, or empty string)
  - traveling_with (brief — "you and your wife" / "you and your kids" /
    "you and your friend Mike" — based on what they told you in
    soft_qual #3 about who's coming)
  - tour_slot (exact slot they chose, e.g. "Wednesday at 2 PM")
  - on_property (true if staying at the property, false if off-property)
  - confirmation_id (from opc_book's return value)
This makes the SMS feel personal — "Hi Sarah, you and your wife are
booked for the {property_name} preview Wednesday at 2 PM..." instead of
a generic blast.

# Memory & continuity (CRITICAL)

You are ONE conversation, not 22 independent forms. ALWAYS use
information the caller has already told you. NEVER ask the same
question twice.

- If they told you about a spouse / partner / family in soft_qual,
  reuse that fact in hard_qual_decision_makers. Don't ask "who would
  attend" again — instead, CONFIRM: "And your wife who's with you on
  this trip — would she be able to come to the preview with you? They
  ask all financial decision-makers to attend together."
- If they said "we always travel as a couple" in soft_qual, you know
  the decision-maker structure already. Confirm rather than re-ask.
- If they mentioned where they're from in soft_qual, count that as
  the answer to hard_qual_residency. Don't re-ask "where do you live."
- If they mentioned occupation or retirement in soft_qual, reuse it
  for hard_qual_employment. Confirm rather than re-ask.
- Carry forward names, slot preferences, and behavioral signals
  across the whole call. The caller is one person, not nine forms.

If a hard_qual gate has ALREADY been answered implicitly during
soft_qual, mark it confirmed silently and move to the next gate.
Don't make the caller repeat themselves — that breaks trust and
sounds robotic.

# Tone

- Warm, calm, concierge — not a salesperson, not an interrogation.
- Short responses. Never monologue more than twelve seconds.
- One question per turn. Wait for the answer.
- Listen for behavioral signals: disposable income, who makes financial
  decisions, travel frequency, openness vs resistance.
- Be patient. Timeshare is not a sought-after product, it is sold. If a
  guest pushes back, acknowledge first, then offer a softer path forward
  OR a clean exit.

# Conversation flow (22-node graph)

You navigate this graph in order. Each state has its own goal. Move on
only when the listed condition is met.

## 1. start_disclosures + 18+ data-consent gate
The greeting already opens with the disclosure AND the 18+ question.
Wait for an answer to "are you eighteen or older?":
- "yes" / 18+ confirmed → step 2 (hook_and_permission)
- "no" / under 18 / refuses → end gracefully with
  exit_reason="under_18". Say: "Got it — I can only book guests who
  are eighteen or older. Thanks for letting me know — have a great
  day." Do NOT collect any further information.
- "are you human / AI?" → answer (use the Identity rules — frame as
  Virtual Booking Agent, acknowledge AI-powered if directly asked),
  RE-ASK 18+ confirmation, then step 2
- objects to recording → end gracefully (recording_or_ai_objection)
- "stop / DNC / remove me / lawyer" → end gracefully (dnc)
- "wrong number / scanned by accident" → end gracefully (wrong_person)

## 2. hook_and_permission
Say with energy: "Great — and thanks. Because you scanned today,
{property_name} is inviting a small number of guests to a short
informational presentation about new vacation ownership programs they're
launching. As a thank-you for your time, qualified guests can lock in
your {premium_offer} — and this is limited, so let's see if you
qualify. I'll just ask a few quick questions, takes about a minute.
What should I call you?"
Capture the caller's first name. Then: "Sound good if I run through
those quick questions?"
- agrees → step 3 (soft_qual)
- time objection → step 19 (obj_time)
- sales-resistance objection → step 20 (obj_sales)
- general "no thanks" → step 22 (obj_general)
- DNC → end gracefully

## 3. soft_qual
Ask these in sequence, ONE AT A TIME, conversationally — not as a
checklist. After each, briefly acknowledge before moving on:
  1. "Are you staying here at {property_name}, or at another hotel
     nearby?" — capture on_property = true if at the property,
     false if elsewhere. THIS DRIVES THE DEPOSIT PATH later.
  2. "How long are you in town for?"
  3. "Who are you traveling with — spouse or partner, family, or friends?"
  4. "How often do you usually take vacations or getaways in a year?"
If asked "why so many questions?": "These are standard eligibility checks
to make sure the offer is a good fit and worth your time."
After all four (or guest gave at least 2 and is willing to continue) →
step 4 (hard_qual_age).

## 4. hard_qual_age
"For this offer, at least one guest attending must be twenty-five or
older and legally able to be on the paperwork. Are you twenty-five or
older?" Yes/no — do not ask exact age.
- yes → step 5
- no or refuses → end gracefully (not_eligible)

## 5. hard_qual_decision_makers
"If you attend, would it be just you, or you and a spouse or partner who
helps with financial decisions?" If they have a partner: "For this offer,
all financial decision-makers must attend together. Would they be able to
come with you?" Single adults attending solo are accepted.
- yes (solo or both will attend) → step 6
- partner can't attend → step 21 (obj_spouse)
- refuses → end gracefully (not_eligible)

## 6. hard_qual_income
"To make sure it's a fit, the resort asks that the household income be at
least about fifty thousand dollars per year. Does your household fall at
or above that? Just a yes or no — I do not need exact numbers." If they
resist: "I understand. I do not need exact income or proof — just a yes
or no, and that information stays only on this call."
- yes → step 7
- no or refuses → end gracefully (not_eligible)

## 7. hard_qual_employment
"And are you currently employed, self-employed, or retired with income?"
Yes/no — do not ask employer or income source detail.
- yes → step 8
- no → end gracefully (not_eligible)

## 8. hard_qual_credit
"They also look for a major credit card in your name — Visa, Mastercard,
Amex, or Discover, not a prepaid card — that you normally use when you
travel. Do you have one in good standing?" Yes/no only.
- IF GUEST STARTS READING CARD DIGITS, INTERRUPT with the PCI hard rule
  above, then re-ask.
- "Is this a credit check?" → "No credit check on this call. Just a yes
  or no eligibility confirmation."
- yes → step 9
- no, prepaid only, in active bankruptcy, or refuses → end gracefully
  (not_eligible)

## 9. hard_qual_prior_tour
"Have you attended a vacation ownership preview at the resort in the last six
to twelve months, or do you have any open or incomplete promotional
packages with them?"
- no to both → step 10
- yes to either → end gracefully (not_eligible)

## 10. hard_qual_residency
"Where do you live most of the year — what city and state?"
- If they say a non-Florida state or city → step 11 (qualified)
- If they say "Florida" alone or anything ambiguous → ASK FOLLOW-UP:
  "What part of Florida — are you near Orlando, or further away?"
  Florida is huge; only Orlando metro / Central Florida (within ~75
  miles of the resort) is excluded. Tampa, Miami, Jacksonville,
  Pensacola, Fort Lauderdale, the Keys — all qualify.
- Only after a CLEAR answer puts them inside Orlando metro → end
  gracefully (not_eligible — local exclusion)
- Refuses even after follow-up → "It's required for offer eligibility
  — could you share at least the city?" Only end gracefully if they
  refuse a second time.

## 11. hard_qual_language
"Is English comfortable for you to follow about a 90-minute presentation,
or would you need another language?"
- English ok → step 12
- needs another language → end gracefully (language_mismatch)

## 12. hard_qual_attendance
"If we find a time that fits your schedule, are you willing to attend
about a 90-minute preview and stay through the full preview to receive
{premium_offer}?"
- yes → step 13 (schedule_offer)
- hesitation / "we'll see" → step 19 (obj_time) — first pass
- hard refuse → end gracefully (not_interested)

## 13. schedule_offer
"Awesome — you qualify for the preview and {premium_offer}. I'll check
what's open. Are mornings or afternoons better for you while you're here?"
Then offer two concrete slots: "I have {slot_1} or {slot_2}. Which one
works better?" If neither works: "What day and rough time works best, and
I'll find the closest slot."
- guest picks a slot → step 14 (deposit_explanation)
- stalls / "maybe later" → step 22 (obj_general) — first pass
- no time works → end gracefully (not_interested)

## 14. deposit_explanation (BRANCHES on on_property captured in step 3)
- IF on_property is true (staying at {property_name}): "Since you're
  staying on property, the resort places a seventy-five dollar deposit on
  your room folio just to hold the time. When you show up on time and
  complete the preview, the deposit comes off — it just confirms you'll
  be there." Capture deposit_path = "folio".
- IF on_property is false (off-property): "Because you're staying off
  property, the resort normally secures the spot with a seventy-five
  dollar deposit. For this pilot, a resort welcome team member will follow up
  separately to handle that part securely — my role today is just to
  qualify you and reserve your time. Is that okay?" Capture
  deposit_path = "team_followup".
- guest accepts → step 15 (confirm_and_sms_consent)
- guest refuses deposit entirely → end gracefully (deposit_refused)

## 15. confirm_and_sms_consent
ASK ONCE — do NOT repeat questions in this step. Single pass.

Read this verbatim, in one breath, then wait:
"Perfect. I'm holding {{slot_chosen}} for the {property_name} preview.
Plan to arrive about fifteen minutes early, bring a photo ID and the
credit card you use when you travel — and plan for about ninety
minutes. I'll text your confirmation and directions — is the number
you're calling from the best one for that?"

Capture sms_consent_captured (true/false) AND the verbatim
sms_consent_phrase in ONE turn. If they say yes/yep/sure → captured.
If they say no → captured=false, do not send. If they say "use a
different number" → ask once for the number, capture it.

Do NOT also do a "reliability check" or ask "anything else" — just go
straight to step 16.

- guest confirms (any answer to SMS) → step 16 (book_tool_call)
- guest wants a different slot → step 13 ONCE, then come back here
- guest pulls out → end gracefully (dnc)

## 16. book_tool_call
Call the `opc_book` tool with all captured fields. Do NOT tell the guest
the booking is complete until the tool returns success.
- success → step 17 (end_confirmed_tour)
- failure → end gracefully (booking_failed)

## 17. end_confirmed_tour (final success)
Speak warmly and naturally — DO NOT sound like you are reading code.
DO NOT say "step seventeen" or "end_confirmed_tour" — those are
internal labels.

Compose the close in your own words from these elements:
  - Confirm the slot: "You're all set for {{slot_chosen}}."
  - SMS reassurance (only if sms_consent was true): "Watch for the
    text with your details — it'll come through in the next minute."
  - Deposit framing — pick ONE based on on_property:
      * on_property=true: "Your seventy-five dollar hold is on your
        room folio and comes right off the moment you arrive."
      * on_property=false: "A team member will reach out separately
        about the seventy-five dollar refundable deposit."
  - Anchor on premium: "Once you complete the full preview, your
    {premium_offer} unlocks."
  - Warm sign-off: "Thanks so much for your time, {caller_first_name}
    — enjoy the rest of your stay, and we'll see you {{slot_chosen}}!"

Wait briefly for any final response from the caller (1-2 seconds).
If they say "thanks" or "bye", reply naturally ("You're welcome —
take care!"), THEN call hangup_call(reason="qualified_and_booked").
Do NOT cut them off mid-goodbye.

## 18. end_graceful (context-aware exit)
Pick the right phrasing for the exit_reason that brought you here:
  - dnc / harassment / anger: "Understood — I'll mark this number as
    do-not-contact for this offer. You will not be contacted again. Have
    a good day."
  - wrong_person / accident / employee: "Got it. I'll close this out so
    this number isn't contacted further. Take care."
  - not_eligible: "Thanks so much for your time. Based on a couple of
    the requirements, this particular offer isn't the best fit today, so
    I'm not able to book the preview. You're welcome to enjoy the resort
    and any other offers at the front desk. Have a wonderful stay."
  - not_interested: "Totally understand — this isn't for everyone. I
    appreciate you chatting with me. Enjoy the rest of your stay."
  - recording_or_ai_objection: "Absolutely — I'll close this out right
    now. Enjoy your day."
  - booking_failed: "I'm sorry — I'm having trouble locking that in on
    my end. A resort welcome team member will reach out to you to finalize.
    Thanks for your patience."
  - deposit_refused: "No problem at all. The deposit is required to hold
    the slot, but a resort welcome team member can talk you through the full
    details. Thanks for your time."
  - language_mismatch: "I'm sorry — I can only assist in English on
    this call. A Spanish-speaking team member can follow up. Take care."
Then call `hangup_call` with the matching reason.

## 19. obj_time (handler — bounce back to caller)
ACKNOWLEDGE first, then 1 rebuttal + 1 trial close. Use lookup_objection
for the right rebuttal, OR speak naturally:
  - "Totally get that — most families don't think they have the time.
    That's why they keep it tight to about ninety minutes. If I could
    get you in and out before lunch and still hook you up with
    {premium_offer}, would that be worth it?"
  - "Exactly why they offer this — so you get something extra out of
    the trip. Would mornings or afternoons feel better?"
- guest opens up → return to whichever node called you
- SAME time objection a SECOND time → end gracefully (not_interested)

## 20. obj_sales (handler)
Acknowledge + reframe:
  - "Perfect — this isn't about buying today. They actually focus more
    on education than pressure."
  - "Most people aren't into timeshares — until they see how it actually
    works now."
  - "Then you'll like this — it's more informational than a sales pitch."
1 rebuttal + 1 trial close.
- opens up → return
- 2nd pass hard "no" → end gracefully (not_interested)

## 21. obj_spouse (handler)
Acknowledge + offer path forward:
  - "They'll need both of you — when are you next together? We can
    schedule for then."
  - "What would they say if there was a benefit tied to it?"
- finds path → return to step 5 (decision_makers)
- 2nd pass / no path → end gracefully (not_eligible)

## 22. obj_general (handler)
Acknowledge + soft trial close:
  - "Totally understand — can I ask what you're most excited about on
    this trip?"
  - "No problem — just curious, do you travel often?"
- engages → return
- 2nd pass → end gracefully (not_interested)

# Tools available

- `lookup_qa(question_text)` — Canonical Arrivia answers (premium,
  presentation, deposit, eligibility, opt-out). USE THIS the FIRST
  time the caller asks any factual question you're not 100% certain
  about. Tracks no_match streaks — if it returns escalate=true,
  call transfer_to_human.
- `lookup_objection(objection_text)` — Top 100 Objections playbook
  rebuttals. Use on any first-pass emotional/sales objection.
- `note_uncertainty(reason)` — CALL THIS BEFORE YOU HEDGE. Any
  "let me check / I'm not sure / great question" phrasing means
  you're hedging. Tracks consecutive hedges; after 2, instructs
  transfer.
- `transfer_to_human(reason, brief)` — Warm-transfer the caller to
  a live specialist. Use when ANY escalation tool tells you to,
  OR when the caller asks for a person. Pass a short brief so
  the specialist picks up cleanly.
- `opc_book(...)` — Book the tour AFTER all 9 gates pass AND slot
  confirmed AND SMS consent captured. Do NOT say "you're booked"
  until this returns success.
- `send_sms_confirmation(...)` — Personalized SMS via SendBlue.
  Call AFTER opc_book returns success and sms_consent_captured was
  true. Pass caller_first_name, traveling_with, slot, on_property,
  confirmation_id.
- `hangup_call(reason)` — End the call cleanly. Use only after the
  caller has had a beat to say goodbye, OR after a successful
  transfer_to_human (the room is then deleted).
- `detect_voicemail()` — If you suspect voicemail / answering
  machine.

# Goal

Book qualified guests on a 90-to-120-minute resort preview tour. You
succeed when the caller has passed all 9 gates, chosen a tour slot,
agreed to the deposit path (folio or team_followup), confirmed SMS
delivery, and the `opc_book` tool returns success.
""".strip()


def render_persona(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_GUEST_CONTEXT, **(ctx or {})}
    return PERSONA_INSTRUCTIONS_TEMPLATE.format(**merged)


def render_greeting(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_GUEST_CONTEXT, **(ctx or {})}
    direction = merged.get("direction", "inbound")
    template = (
        GREETING_INSTRUCTIONS_OUTBOUND_TEMPLATE
        if direction == "outbound"
        else GREETING_INSTRUCTIONS_INBOUND_TEMPLATE
    )
    return template.format(**merged)


def parse_metadata(raw: str | None) -> dict[str, str]:
    """Pull guest-context fields out of `ctx.job.metadata` JSON.

    Unknown keys are kept (they may be used by downstream tools); missing
    keys fall back to DEFAULT_GUEST_CONTEXT inside the renderers.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ctx.job.metadata is not valid JSON, ignoring")
        return {}
    return {k: str(v) for k, v in data.items() if v is not None}


class VBAQualifierAgent(Agent):
    """Phase 1 agent — greeting + objection lookup.

    Phase 1B will add the qualification state machine.
    Phase 1C will add the rest of the eight tools.
    """

    def __init__(self, guest_context: dict[str, str] | None = None) -> None:
        merged_ctx = {**DEFAULT_GUEST_CONTEXT, **(guest_context or {})}
        # CRITICAL: super().__init__() MUST be called before we set
        # any instance attrs that aren't part of Agent's required
        # init sequence. The base Agent class expects to set _id and
        # other internal attributes during its own init. Setting
        # custom attrs before super() doesn't crash on construction
        # but does crash later in session.start() when label/id is
        # accessed. (Caught from runtime logs after a deploy.)
        super().__init__(instructions=render_persona(merged_ctx))
        self._guest_context = merged_ctx
        # Escalation counters — when any hits its threshold, the
        # relevant tool returns an "escalate now" instruction so
        # Deedy hands off to a human or scheduler-link instead of
        # spiraling. Reset to 0 whenever the agent makes forward
        # progress (successful lookup_qa match, gate passed, etc.)
        self._escalation = {
            "qa_no_match_streak": 0,
            "uncertainty_streak": 0,
            "repeat_question_streak": 0,
            "last_question": "",
        }

    def _bump_escalation(self, key: str) -> int:
        self._escalation[key] = int(self._escalation.get(key, 0)) + 1
        return self._escalation[key]

    def _reset_escalation(self) -> None:
        self._escalation["qa_no_match_streak"] = 0
        self._escalation["uncertainty_streak"] = 0
        self._escalation["repeat_question_streak"] = 0

    @function_tool(
        name="opc_book",
        description=(
            "Book the resort preview tour AFTER the guest passes ALL nine "
            "qualification gates AND has chosen a tour slot AND has agreed "
            "to the deposit path AND given SMS consent. This is the final "
            "commit step — do NOT call it earlier. Do NOT tell the guest "
            "the booking is confirmed until this returns success. On "
            "success, follow up with send_sms_confirmation."
        ),
    )
    async def opc_book(
        self,
        caller_phone: str,
        tour_slot: str,
        on_property: bool,
        deposit_path: str,
        sms_consent_captured: bool,
        sms_consent_phrase: str = "",
        caller_name: str = "",
    ) -> dict:
        """Calls the live OPC booking endpoint at arrivia-gvr.vercel.app.

        Args:
            caller_phone: E.164 phone number, e.g. "+14078195809"
            tour_slot: Human-readable slot, e.g. "tomorrow at 10:30 AM"
            on_property: True if guest is staying at the property
            deposit_path: "folio" or "team_followup"
            sms_consent_captured: True if guest agreed to text confirmations
            sms_consent_phrase: Verbatim guest words when consenting
            caller_name: Guest's name if captured
        """
        import httpx

        url = os.environ.get(
            "OPC_BOOK_URL",
            "https://arrivia-gvr.vercel.app/api/tools/opc-book",
        )
        api_key = os.environ.get("OPC_BOOK_API_KEY", "")
        payload = {
            "caller_phone": caller_phone,
            "caller_name": caller_name or self._guest_context.get("caller_name", ""),
            "tour_slot": tour_slot,
            "on_property": on_property,
            "deposit_path": deposit_path,
            "sms_consent_captured": sms_consent_captured,
            "sms_consent_phrase": sms_consent_phrase,
            "placement_name": self._guest_context.get("placement_name", ""),
            "incentive": self._guest_context.get("premium_offer", ""),
            "property_name": self._guest_context.get("property_name", ""),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    json=payload,
                    headers={"x-api-key": api_key} if api_key else {},
                )
            if r.status_code >= 400:
                logger.warning("opc_book failed: %s %s", r.status_code, r.text[:200])
                return {"success": False, "error": f"http_{r.status_code}"}
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            confirmation_id = data.get("confirmation_id") or data.get("id") or "TBD"
            logger.info("opc_book ok: confirmation=%s", confirmation_id)

            # Dashboard appointment record. Best-effort — never block
            # the booking response on this.
            try:
                ctx_room = agents.get_job_context()
                room_name = ctx_room.room.name if ctx_room and ctx_room.room else ""
                _fire_telemetry(
                    room_name,
                    "appointment",
                    {
                        "caller_name": caller_name or self._guest_context.get("caller_name", ""),
                        "caller_phone": caller_phone,
                        "property_name": self._guest_context.get("property_name", ""),
                        "placement_slug": self._guest_context.get("placement_slug", ""),
                        "tour_slot": tour_slot,
                        "on_property": on_property,
                        "deposit_path": deposit_path,
                        "confirmation_id": confirmation_id,
                        "status": "booked",
                    },
                )
            except Exception:
                pass

            return {"success": True, "confirmation_id": confirmation_id}
        except Exception as e:
            logger.warning("opc_book exception: %s", e)
            return {"success": False, "error": str(e)}

    @function_tool(
        name="send_sms_confirmation",
        description=(
            "Send a PERSONALIZED booking confirmation by SMS / iMessage via "
            "SendBlue. Call this AFTER opc_book returns success and only if "
            "sms_consent_captured was true. Pass everything you learned on "
            "the call: caller's first name (if you captured one), the exact "
            "tour slot they chose, on/off-property status, and the "
            "confirmation_id from opc_book. The tool composes the message "
            "using those fields. NEVER include card / payment data."
        ),
    )
    async def send_sms_confirmation(
        self,
        to_phone: str,
        tour_slot: str,
        on_property: bool,
        confirmation_id: str = "",
        caller_first_name: str = "",
        traveling_with: str = "",
    ) -> dict:
        """Sends a personalized confirmation text via SendBlue.

        Args:
            to_phone: E.164 phone, e.g. "+14078195809"
            tour_slot: Exact slot the caller chose, e.g. "Wednesday at 2 PM"
            on_property: True = staying at the resort (folio hold path);
                False = off-property (team-followup deposit path)
            confirmation_id: Booking id returned by opc_book
            caller_first_name: First name if captured during the call
                (e.g. "Ethan"). Empty string if not captured.
            traveling_with: Brief, e.g. "you and your wife" or "your family",
                if the caller mentioned who's coming. Empty if unknown.
        """
        import httpx

        api_key = os.environ.get("SENDBLUE_API_KEY_ID")
        api_secret = os.environ.get("SENDBLUE_API_SECRET_KEY")
        if not api_key or not api_secret:
            return {"success": False, "error": "sendblue_not_configured"}

        property_name = self._guest_context.get(
            "property_name", "the resort"
        )
        offer = self._guest_context.get(
            "premium_offer", "your premium offer"
        )

        # --- Build the personalized message ---
        greet = (
            f"Hi {caller_first_name.strip()}, " if caller_first_name.strip()
            else ""
        )
        attendees = (
            f"{traveling_with.strip()} are " if traveling_with.strip()
            else "you're "
        )
        deposit_line = (
            "Your $75 hold is on your room folio at the resort and "
            "comes off the moment you arrive."
            if on_property
            else
            "A resort welcome team member will reach out shortly to handle the "
            "$75 refundable deposit securely."
        )
        confirmation_line = (
            f" Confirmation: {confirmation_id}."
            if confirmation_id and confirmation_id != "TBD"
            else ""
        )

        body = (
            f"{greet}{attendees}booked for the {property_name} preview "
            f"{tour_slot}.{confirmation_line} "
            f"Arrive about 15 minutes early — bring a photo ID and the "
            f"credit card you use when you travel. Plan for about 90 "
            f"minutes. Once you complete the full preview, your "
            f"{offer} unlocks. "
            f"{deposit_line} "
            f"— Deedy at {property_name}"
        )

        from_number = os.environ.get("SENDBLUE_FROM_NUMBER")
        if not from_number:
            return {"success": False, "error": "sendblue_from_number_not_configured"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    "https://api.sendblue.co/api/send-message",
                    json={
                        "number": to_phone,
                        "from_number": from_number,
                        "content": body,
                    },
                    headers={
                        "sb-api-key-id": api_key,
                        "sb-api-secret-key": api_secret,
                        "Content-Type": "application/json",
                    },
                )
            if r.status_code >= 400:
                logger.warning("sendblue failed: %s %s", r.status_code, r.text[:200])
                return {"success": False, "error": f"http_{r.status_code}"}
            logger.info("sendblue sms sent to %s", to_phone)
            return {"success": True}
        except Exception as e:
            logger.warning("sendblue exception: %s", e)
            return {"success": False, "error": str(e)}

    @function_tool(
        name="detect_voicemail",
        description=(
            "Call this if you suspect you've reached a voicemail / "
            "answering machine instead of a live human. Signals: long "
            "uninterrupted greeting, 'leave a message after the tone', "
            "robotic phrasing, no responses to your questions, the same "
            "phrase repeating, or beep/tone audio. After calling this, "
            "leave a SHORT message: 'Hi, this is Deedy from {property_name} "
            "Lakes — I'll text you a link to reschedule.' Then call "
            "hangup_call(reason='voicemail')."
        ),
    )
    async def detect_voicemail(self) -> dict:
        """Marks the call as voicemail in logs. Agent then leaves a
        short message and hangs up."""
        logger.info("detect_voicemail: agent classified the line as voicemail")
        # Render the property name from guest context — never let the
        # raw `{property_name}` template literal reach the LLM.
        prop = self._guest_context.get("property_name", "the resort")
        return {
            "is_voicemail": True,
            "instruction": (
                f"Leave a 5-second message: 'Hi, this is Deedy from "
                f"{prop} — I'll text you a link to reschedule.' Then "
                f"call hangup_call with reason='voicemail'. Do NOT run "
                f"the qualification flow against a voicemail box."
            ),
        }

    @function_tool(
        name="hangup_call",
        description=(
            "End the call cleanly. Call this after end_confirmed_tour or "
            "any end_graceful path is complete. The reason field tags the "
            "exit for analytics — use one of: qualified_and_booked, "
            "not_eligible, not_interested, dnc, wrong_person, "
            "recording_or_ai_objection, deposit_refused, language_mismatch, "
            "booking_failed, voicemail, transferred_to_human."
        ),
    )
    async def hangup_call(self, reason: str = "qualified_and_booked") -> dict:
        """Deletes the room, which disconnects all participants."""
        from livekit import api as lk_api
        from livekit.agents import get_job_context

        ctx = get_job_context()
        if ctx is None:
            return {"ended": False, "error": "no_job_context"}
        try:
            await ctx.api.room.delete_room(
                lk_api.DeleteRoomRequest(room=ctx.room.name),
            )
            logger.info("hangup_call: reason=%s", reason)
            return {"ended": True, "reason": reason}
        except Exception as e:
            logger.warning("hangup_call exception: %s", e)
            return {"ended": False, "error": str(e)}

    @function_tool(
        name="lookup_qa",
        description=(
            "Look up the canonical Arrivia answer for a guest factual "
            "question. Use this whenever the caller asks something about "
            "the premium, the presentation, the deposit, eligibility, "
            "rescheduling, opt-out, or any factual detail you might not "
            "know off the top of your head. Speak the returned answer "
            "naturally in your own warm tone — do not read it word-for-word "
            "if it sounds stilted. Returns no_match if nothing close, in "
            "which case say honestly: 'Great question — the welcome team "
            "can confirm that when you arrive.'"
        ),
    )
    async def lookup_qa(self, question_text: str) -> dict:
        """Tool: pull the best-matching canonical answer.

        Args:
            question_text: The caller's question, paraphrased into a
                short clear sentence (e.g. "what is the premium",
                "how long is the presentation", "do both spouses
                attend").
        """
        # Repeat-question detection — if the same question_text
        # arrives twice in a row, the caller is stuck in a loop OR
        # Deedy isn't moving the conversation forward. Both signal
        # escalation.
        normalized = " ".join(question_text.lower().split())
        if normalized and normalized == self._escalation.get("last_question"):
            streak = self._bump_escalation("repeat_question_streak")
            if streak >= 2:
                logger.info("escalating: repeat_question_streak=%d", streak)
                return {
                    "no_match": False,
                    "escalate": True,
                    "reason": "repeat_question",
                    "instruction": (
                        "The caller has asked this same question 3+ "
                        "times — your previous answers aren't landing. "
                        "Stop trying to answer. Say warmly: 'I want to "
                        "make sure you get a clear answer on this — "
                        "let me get a specialist on the line who can "
                        "walk you through it.' Then call "
                        "transfer_to_human(reason='factual_confusion')."
                    ),
                }
        else:
            self._escalation["repeat_question_streak"] = 0
        self._escalation["last_question"] = normalized

        matches = match_qa(question_text)
        if not matches:
            streak = self._bump_escalation("qa_no_match_streak")
            if streak >= 2:
                logger.info("escalating: qa_no_match_streak=%d", streak)
                return {
                    "no_match": True,
                    "escalate": True,
                    "reason": "qa_unknown",
                    "instruction": (
                        "You've now hit two factual questions in a row "
                        "you don't have answers for. Don't keep guessing. "
                        "Say warmly: 'These are great questions and I "
                        "want to make sure you get accurate answers — "
                        "let me get a specialist on the line.' Then "
                        "call transfer_to_human(reason='qa_unknown')."
                    ),
                }
            return {
                "no_match": True,
                "guidance": (
                    "Acknowledge the question, then say honestly: "
                    "'Great question — the welcome team can confirm "
                    "that when you arrive.' Then move forward in the "
                    f"flow. (no_match streak: {streak}/2 — escalate at 2)"
                ),
            }
        # match found → reset streaks (we made forward progress)
        m = matches[0]
        self._escalation["qa_no_match_streak"] = 0
        self._escalation["uncertainty_streak"] = 0
        return {
            "no_match": False,
            "section": m.section,
            "matched_question": m.question,
            "answer": m.answer,
            "score": round(m.score, 3),
            "instruction": (
                "Speak the answer naturally in your own warm tone. "
                "Don't read it stiffly. Then ask if there's anything "
                "else, or move forward in the flow."
            ),
        }

    @function_tool(
        name="note_uncertainty",
        description=(
            "Call this BEFORE you say something hedge-y like 'let me "
            "check', 'I'm not sure about that', 'that's a great "
            "question', 'I'd have to look that up', or any phrasing "
            "that signals you don't actually know the answer. Pass a "
            "short reason describing what you're uncertain about. The "
            "tool tracks how often you've had to hedge — after the "
            "second hedge in a row without forward progress, it "
            "instructs you to escalate to a human specialist instead "
            "of spiraling. Use this honestly — hedging twice means "
            "you're wasting the caller's time."
        ),
    )
    async def note_uncertainty(self, reason: str) -> dict:
        streak = self._bump_escalation("uncertainty_streak")
        logger.info("note_uncertainty: reason=%r streak=%d", reason, streak)
        if streak >= 2:
            return {
                "escalate": True,
                "streak": streak,
                "instruction": (
                    "You've now hedged twice in a row — that's enough. "
                    "Don't keep saying 'let me check'. Say warmly: 'I "
                    "want to make sure you get the right answer on "
                    "that — let me get a specialist on the line who "
                    "can walk you through it.' Then call "
                    "transfer_to_human(reason='hedging_loop')."
                ),
            }
        return {
            "escalate": False,
            "streak": streak,
            "instruction": (
                "OK — go ahead and acknowledge you're not sure, but "
                "DON'T just hedge. Either (a) call lookup_qa with the "
                "exact question, or (b) defer cleanly to the welcome "
                "team and move the conversation forward. Note: if you "
                "hedge again without forward progress, you'll need to "
                "transfer."
            ),
        }

    @function_tool(
        name="transfer_to_human",
        description=(
            "Warm-transfer the caller to a live human specialist. Use "
            "ONLY when an escalation tool has explicitly told you to "
            "(qa_unknown, hedging_loop, repeat_question, or the caller "
            "directly asks for a person). Pass a brief — the specialist "
            "uses it to pick up where you left off. Live agent number "
            "is configured via LIVE_AGENT_NUMBER env var."
        ),
    )
    async def transfer_to_human(self, reason: str, brief: str = "") -> dict:
        """Warm-transfer the caller via DIAL-AND-BRIDGE (not SIP REFER).

        We dial the live agent via the LiveKit outbound trunk and add
        them as a participant in the SAME room as the caller. Caller
        never leaves; observability/recording/transcript all keep
        running. See docstring on Andie.transfer_to_specialist for
        full rationale.

        On failure (busy, no-answer, no outbound trunk configured),
        returns an error so the LLM can apologize and offer to
        schedule a callback instead.
        """
        ctx = agents.get_job_context()
        if ctx is None:
            return {"transferred": False, "error": "no_job_context"}

        target = os.environ.get("LIVE_AGENT_NUMBER", "")
        if not target or target.startswith("+1555") or target == "+10000000000":
            logger.warning("transfer_to_human: LIVE_AGENT_NUMBER missing/placeholder")
            return {"transferred": False, "error": "live_agent_not_configured"}

        outbound_trunk = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
        if not outbound_trunk:
            return {"transferred": False, "error": "outbound_trunk_not_configured"}

        sip_p = next(
            (
                p for p in ctx.room.remote_participants.values()
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
            ),
            None,
        )
        if sip_p is None:
            return {"transferred": False, "error": "no_sip_participant"}

        # Caller-ID for the dial-out leg.
        caller_id = (
            os.environ.get("LIVEKIT_PHONE_NUMBER")
            or os.environ.get("TWILIO_VOICE_NUMBER")
            or "+14072890294"
        )
        agent_identity = f"agent-{target.lstrip('+')}"

        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=outbound_trunk,
                    sip_call_to=target,
                    sip_number=caller_id,
                    participant_identity=agent_identity,
                    participant_name="Resort Specialist",
                    krisp_enabled=True,
                    wait_until_answered=True,
                )
            )
        except api.TwirpError as e:
            sip_code = e.metadata.get("sip_status_code") if e.metadata else None
            logger.warning(
                "agent_dial_failed reason=%s sip=%s msg=%s",
                reason, sip_code, e.message,
            )
            return {
                "transferred": False,
                "error": "live_agent_unavailable",
                "sip_status_code": sip_code,
            }
        except Exception as e:
            logger.warning("transfer_to_human unexpected: %s", e)
            return {"transferred": False, "error": str(e)}

        logger.info(
            "agent_bridged target=%s reason=%s brief=%r room=%s",
            target, reason, brief[:160], ctx.room.name,
        )

        # Brief the specialist with the caller still on the line, then
        # close the agent session so the humans take it from here.
        try:
            session = self.session  # type: ignore[attr-defined]
            caller_name = self._guest_context.get("caller_name", "the guest")
            handoff_line = (
                f"Hi, this is Deedee — connecting you with {caller_name}. "
                f"Quick brief: {_truncate_at_word(brief, 200) if brief else reason}. "
                f"I'll let you take it from here."
            )
            await session.say(handoff_line, allow_interruptions=False)
            await session.aclose()
        except Exception as e:
            logger.warning("handoff_line failed (bridge still up): %s", e)

        return {
            "transferred": True,
            "target": target,
            "reason": reason,
            "method": "dial_and_bridge",
        }

    @function_tool(
        name="lookup_objection",
        description=(
            "Look up the recommended rebuttal for a caller's objection from "
            "the Top 100 Objections playbook. Call this whenever the caller "
            "raises hesitation, resistance, or pushback (e.g. about time, "
            "price, spouse, money, trust, prior bad experience). Returns the "
            "matched category, the canonical objection, the rebuttal to "
            "speak, and a confidence score. If no_match is true, improvise "
            "a warm short response in your own voice and follow with a soft "
            "trial close."
        ),
    )
    async def lookup_objection(self, objection_text: str) -> dict:
        """Tool: surface the best-matching rebuttal.

        Args:
            objection_text: The caller's exact words, paraphrased into a
                short objection sentence (e.g. "they don't have time",
                "they think it's all a sales pitch", "spouse isn't here").
        """
        matches = match_objection(objection_text)
        if not matches:
            return {
                "no_match": True,
                "objection_text": objection_text,
                "guidance": (
                    "Acknowledge warmly, validate the concern in one short "
                    "line, then follow with a soft trial close that re-asks "
                    "for the booking time."
                ),
            }
        m = matches[0]
        incentive = self._guest_context.get("incentive")
        return {
            "no_match": False,
            "category": m.category,
            "matched_objection": m.objection,
            "rebuttal": render_rebuttal(m.rebuttal, incentive),
            "score": round(m.score, 3),
            "instruction": (
                "Speak the rebuttal naturally in your own warm tone. End "
                "with a soft trial close ('Does morning or afternoon work "
                "better?')."
            ),
        }


async def entrypoint(ctx: JobContext) -> None:
    """Entrypoint for the named agent dispatch.

    Recommended LiveKit pattern (per docs/livekit-outbound-calls.md):
    the orchestrator (apps/web `/api/dial`) only does AgentDispatch with
    metadata = {"phone_number": "+1...", "resort_name": ..., ...}. The
    agent itself creates the SIP participant, waits until the callee
    picks up, then starts the session and speaks the greeting.

    For inbound calls (post-MVP), there's no phone_number in metadata —
    the SIP participant has already been routed in by a dispatch rule
    and joined the room before us.
    """
    # Read guest context from job metadata (named-dispatch path) OR
    # room metadata (auto-dispatch path). Either works.
    await ctx.connect()
    guest_ctx = parse_metadata(ctx.job.metadata)
    if not guest_ctx and ctx.room.metadata:
        guest_ctx = parse_metadata(ctx.room.metadata)
    phone_number = guest_ctx.get("phone_number")
    # If a phone_number is in metadata, the orchestrator told us to
    # dial — that's outbound. Otherwise this is inbound.
    if phone_number and "direction" not in guest_ctx:
        guest_ctx["direction"] = "outbound"
    elif "direction" not in guest_ctx:
        guest_ctx["direction"] = "inbound"
    logger.info(
        "joining room=%s job_id=%s direction=%s phone=%s",
        ctx.room.name,
        ctx.job.id,
        guest_ctx["direction"],
        phone_number or "<inbound>",
    )

    # Wait for the SIP participant to join the room. Same code path
    # for inbound and outbound — the agent doesn't care how the caller
    # got into the room.
    try:
        if phone_number:
            sip_participant = await ctx.wait_for_participant(identity=phone_number)
        else:
            sip_participant = await ctx.wait_for_participant()
        logger.info("participant joined: identity=%s", sip_participant.identity)

        # If we don't have a phone_number yet (inbound path — no
        # metadata from dispatch rule), pull it from the SIP
        # participant's attributes. Future opc_book / SMS calls need it.
        if not phone_number:
            attrs = getattr(sip_participant, "attributes", {}) or {}
            sip_phone = attrs.get("sip.phoneNumber") or attrs.get("sip.from")
            if sip_phone:
                guest_ctx["caller_phone"] = sip_phone
                guest_ctx["phone_number"] = sip_phone
                logger.info("inbound caller phone: %s", sip_phone)
    except Exception as e:
        logger.warning("never saw participant: %s", e)
        ctx.shutdown()
        return

    # ---- mid-call hangup detection ----
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p) -> None:  # type: ignore[no-untyped-def]
        if sip_participant and p.identity != sip_participant.identity:
            return
        reason = getattr(p, "disconnect_reason", None)
        logger.info("caller disconnected reason=%s", reason)

    # ---- ORCHESTRATOR-SIDE escalation telemetry (Phase 1 of item #5
    # from the Perplexity multi-model architecture review).
    # These hooks log signals that downstream Phase 2 work can use to
    # auto-escalate without depending on the LLM self-reporting via
    # note_uncertainty. For now they emit logs only — the persona's
    # tool-side counters remain authoritative.
    _agent_ref = {"agent": None}  # populated after session.start

    @ctx.room.on("participant_active_speakers_changed")
    def _on_speakers_changed(speakers) -> None:  # type: ignore[no-untyped-def]
        # Heuristic: if both the SIP caller and the agent are flagged
        # active simultaneously many times in a short window, that's
        # an "overlap / repair-loop" signal worth escalating later.
        # Phase 2 will count overlaps in a sliding window and bump
        # the agent's _escalation["repeat_question_streak"] when a
        # threshold is crossed. For now: log only.
        try:
            ids = [getattr(s, "identity", "?") for s in speakers]
            if len(ids) >= 2:
                logger.debug("active_speakers overlap=%s", ids)
        except Exception:
            pass

    # All three providers via LiveKit Inference — no provider API keys
    # needed (Deepgram, Rime, xAI all bill through LiveKit Cloud).
    #
    #   STT: Deepgram Flux (English) — semantic+acoustic turn detection
    #        built into the model. Pair with turn_detection="stt" for
    #        more natural conversational pacing than VAD alone.
    #   LLM: Grok-4.1 Fast (non-reasoning) — fastest Grok variant
    #   TTS: Rime Arcana, voice="luna" — warm, friendly female; chill
    #        but excitable — matches Deedy's concierge persona. Swap to
    #        'celeste' or 'astra' if Luna doesn't land.
    #   VAD: Silero — handles barge-in / interruption while Flux's STT
    #        endpointing handles turn detection.
    # --- INFRA FAILOVER (LiveKit FallbackAdapter) ---
    # Per multi-model architecture review: implement sequential
    # provider failover for infra/API errors (timeouts, 5xx, rate
    # limits) BEFORE inventing custom complexity routing. If a
    # provider has a regional outage, the call survives and the
    # adapter resubmits to the next provider in the chain.
    #
    # NOTE on personality drift (Claude Opus warning): voices and
    # response styles differ across models/providers. Fallback chains
    # below are stylistically as close as possible to the primary —
    # we'd rather sound slightly different mid-call than drop into
    # dead air during an xAI outage.
    from livekit.agents.llm import FallbackAdapter as LLMFallback
    from livekit.agents.stt import FallbackAdapter as STTFallback
    from livekit.agents.tts import FallbackAdapter as TTSFallback

    primary_stt = inference.STT(
        model="deepgram/flux-general",
        language="en",
        extra_kwargs={
            # 0.7 / 0.9 / 2000ms — less eager than the default 0.4
            # to prevent mid-sentence cutoffs.
            "eager_eot_threshold": 0.7,
            "eot_threshold": 0.9,
            "eot_timeout_ms": 2000,
        },
    )
    # Deepgram Nova-3 as STT fallback — different model family,
    # similar accuracy, no Flux-specific endpointing params.
    fallback_stt = inference.STT(model="deepgram/nova-3", language="en")

    primary_llm = inference.LLM(
        # Grok 4.20 follows long instructions better than 4.1-fast.
        model="xai/grok-4.20-0309-non-reasoning",
        extra_kwargs={
            "temperature": 0.0,
            "max_completion_tokens": 400,
            "parallel_tool_calls": False,
        },
    )
    # Fallback chain: Grok 4.1 fast → openai gpt-4.1-mini.
    # 4.1-fast is the cheapest deterministic Grok variant.
    # GPT-4.1-mini is a different provider entirely so an xAI-wide
    # outage doesn't kill the call.
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
        extra_kwargs={
            "temperature": 0.0,
            "max_completion_tokens": 400,
        },
    )

    primary_tts = inference.TTS(
        model="rime/mistv3",
        voice="lagoon",
        language="en",
    )
    # Rime arcana = same provider, different model family. If
    # Rime is fully down we fall through to Cartesia (different
    # provider) so the call doesn't go silent.
    fallback_tts_arcana = inference.TTS(
        model="rime/arcana",
        voice="luna",
        language="en",
    )
    fallback_tts_cartesia = inference.TTS(
        model="cartesia/sonic-2",
        voice="warm-female",
        language="en",
    )

    session = AgentSession(
        # Tighten STT defaults — the framework default is
        # attempt_timeout=10s which sits the caller in dead air for
        # 10 full seconds before failover. Phone calls demand <4s.
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
        # Flux semantic turn detection — accounts for what the caller
        # said, not just pause length.
        turn_handling=TurnHandlingOptions(turn_detection="stt"),
        # Detect voicemail / IVR and exit cleanly instead of running
        # the qualification flow against an answering machine.
        ivr_detection=True,
        # Allow the caller to interrupt Deedy ("barge-in"). Real
        # callers cut in to ask questions or push back; the agent
        # must yield rather than steamroll.
        allow_interruptions=True,
        # Don't false-trigger on background noise / 'uh' / 'um' — only
        # treat sustained speech (>=2 words) as a real interruption.
        min_interruption_words=2,
        min_interruption_duration=0.4,
    )

    # AgentSession.start() in livekit-agents 1.5.7 does NOT accept a
    # `participant=` kwarg — the session picks up audio from whoever
    # is in the room. The participant we just waited for is now
    # speaking into the same room as us, so this just works.
    # Per-call usage telemetry — surfaces tokens / chars / audio-secs to
    # logs AND POSTs to the arrivia-gvr dashboard for live ops view.
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
        # Generate per-call summary first (uses session chat_ctx), then
        # POST the shutdown event so the dashboard sees both.
        try:
            await _generate_call_summary(session, ctx.room.name, guest_ctx)
        except Exception:
            pass
        try:
            await _post_agent_event(
                ctx.room.name, "shutdown", {"shutdown_reason": reason}
            )
        except Exception:
            pass

    ctx.add_shutdown_callback(_on_shutdown)

    # Recording is fire-and-forget so it CANNOT block session.start().
    # No-op unless RECORDING_ENABLED=1 + storage config set on the agent.
    _start_room_recording_in_background(ctx)

    # Pin audio input to the SIP caller specifically. Without this,
    # `session.start()` picks up "whoever is in the room" — fine for
    # single-participant inbound, but on outbound (after
    # wait_until_answered=True) there's a brief window where the dial
    # leg can co-exist with the answered leg and routing is ambiguous.
    # Filtering by participant_identity removes the race.
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
        agent=VBAQualifierAgent(guest_context=guest_ctx),
        room=ctx.room,
        room_input_options=room_input,
    )

    # Speak first AFTER the callee is in the room. For outbound, this
    # avoids the "greeting plays into ringback" trap. The caller
    # consented to an AI call on the QR page and is expecting the
    # disclosure in the first 10 seconds (FCC PEWC requirement).
    await session.generate_reply(instructions=render_greeting(guest_ctx))


def cli_main() -> None:
    """Console entrypoint exposed as `vba-worker`.

    Uses EXPLICIT dispatch (`agent_name="deedy-vba"`) per LiveKit's
    own recommendation:

      "Automatic dispatch is not recommended for most applications.
       It dispatches an agent to every new room, regardless of whether
       one is needed, and doesn't support passing metadata to the
       agent session."

    With Andie deployed alongside Deedy in the same project, auto-
    dispatch would cause BOTH agents to join every call. Explicit
    dispatch + dispatch-rule-scoped roomConfig.agents fixes that.

    The corresponding dispatch rule (SDR_ito8WVmoAGkV) must include:
      roomConfig.agents = [{ agentName: "deedy-vba", metadata: ... }]
    or no agent will be dispatched and the room sits empty.
    """
    agents.cli.run_app(
        WorkerOptions(entrypoint_fnc=entrypoint, agent_name="deedy-vba")
    )


# Alias so `python -m voxaris_agent.worker` works as well as the script.
cli = cli_main


if __name__ == "__main__":
    cli_main()
