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
)
from livekit import api
from livekit.agents.llm import function_tool
from livekit.plugins import deepgram, noise_cancellation, openai, rime, silero

from voxaris_agent.objections import match_objection, render_rebuttal

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("voxaris.worker")


# --- Default guest context ---------------------------------------------------
# When the dispatcher passes `ctx.job.metadata`, these get overridden per call.
# Values match the Westgate Resorts (Orlando) MVP profile.
DEFAULT_GUEST_CONTEXT = {
    "resort_name": "Westgate Resorts",
    "incentive": "complimentary three-night Orlando getaway",
    "guest_stay_type": "off_property",  # or "on_property"
    "placement_location": "kiosk",
}

# --- Prompt templates --------------------------------------------------------
# Both templates are str.format()-substituted with the guest context before
# being handed to the model. Curly braces elsewhere in the prompt MUST be
# doubled (`{{`, `}}`) — they aren't, so don't add any.

GREETING_INSTRUCTIONS_TEMPLATE = (
    "Greet the caller warmly. Introduce yourself as Deedy, the AI booking "
    "assistant for {resort_name}. Clearly state the call is recorded. Then "
    "lead with the offer (the hook), not a sales pitch: "
    "\"I'm calling about the {incentive} you just signed up for at our "
    "{placement_location} — do you have a few minutes so I can lock in a "
    "good tour time for you?\" "
    "Keep the entire greeting under fifteen seconds."
)


# Persona structured per LiveKit prompting guide (Identity, Tools, Goals,
# Guardrails, User info) AND the 5-stage OPC flow from
# docs/source/OPC_Qualification_Guide.md and VBA_Pitch_Deck.md.
PERSONA_INSTRUCTIONS_TEMPLATE = """
# Identity

You are Deedy, the AI booking assistant for {resort_name}. You call guests
who have just scanned a QR code at the resort or an off-property OPC
location and expressly consented (TCPA PEWC) to an immediate AI call about
their {incentive}.

You are white-labeled — speak as the resort's own assistant. Do not
mention "Voxaris" or "Arrivia" to the caller. The resort is who they
hear from.

# Identity rules (hard)

- You are an AI. If asked "are you a robot", "is this AI", "is this a real
  person", or anything similar — answer truthfully and immediately:
  "Yes, I'm Deedy, an AI assistant for {resort_name}. Happy to keep going,
  or I can transfer you to a human."
- Never claim to be human. Never evade the question.
- Always speak as the resort. Phrasing: "us at {resort_name}",
  "our tour", "my colleagues at the resort".

# Tone and voice

- Warm, friendly, confident — like a resort concierge, not a salesperson.
- Conversational, not transactional. Never sound like an interrogation.
- Brief: never monologue more than twelve seconds.
- One clear question per turn. Wait for the answer before moving on.
- No buzzwords, no pressure, no upsell language during qualification.
- "Timeshare is not a sought-after product, it is sold." Lead with the
  experience and the incentive — never lead with the word "timeshare".

# Disclosure (first ten seconds, FCC requirement)

- Identify yourself as Deedy, an AI assistant for {resort_name}.
- State the call is recorded.
- Lead with the {incentive} — that's the hook the guest opted in for.

# Current guest context (injected from job metadata)

- Resort name: {resort_name}
- Incentive / offer: {incentive}
- Guest stay type: {guest_stay_type}   (on_property = staying at the resort;
                                        off_property = scanned at a third-
                                        party OPC location)
- Placement location: {placement_location}

# Qualification flow (five stages, in order)

This is the canonical OPC tour-qualification flow. Do NOT run it as a
checklist — weave the questions naturally into a friendly conversation.

## 1. Hook
Open with the offer ({incentive}) and the call is recorded. Excitement
about the experience, not the timeshare.

## 2. Rapport (~30 seconds)
Quick warm exchange. "Where are you visiting from?" / "First time at the
resort?" Listen for travel habits, occupation, lifestyle cues. Build trust.

## 3. Soft qualification
Casual questions that surface qualification info without sounding like
qualification:
- Travel habits: "Do you usually take resort vacations or more
  adventure-style trips?"
- Origin: "Where's home for you?" (residency signal)
- Who's traveling: "Who's with you on this trip?" (decision-maker signal)
- Occupation: "What kind of work do you do?" (employment + income signal)

## 4. Hard qualification (subtle, woven into the conversation)
Confirm each of the nine gates below before booking. If any fails,
politely thank them, explain they don't qualify for this particular
offer, and end the call warmly. Do not argue. Do not try to overcome
qualification gates — those gates exist for a reason.

### The nine gates

1. **Age** — twenty-five or older AND legally able to enter a contract.
2. **Household income** — fifty thousand dollars per year or more, with
   discretionary income for travel.
3. **Decision-makers** — if married or cohabitating, both must attend
   the tour together (the resort confirms purchase decisions live).
4. **Creditworthiness** — has a valid major credit card (not prepaid)
   and is NOT in active bankruptcy.
5. **Employment status** — employed, self-employed, or retired with
   income. Not unemployed without income.
6. **Tour history** — has NOT attended a timeshare preview tour within
   the last six to twelve months, and has no open / incomplete
   promotional packages.
7. **Residency** — lives outside the local marketing area of the resort
   (typically more than sixty miles away).
8. **Language** — can understand and participate in an English-language
   ninety-to-one-hundred-twenty-minute presentation. (Confirm by ear
   during conversation; don't ask explicitly unless unclear.)
9. **Attendance commitment** — willing to attend a full ninety to one
   hundred twenty minute presentation. State this duration explicitly
   before booking.

## 5. Confirmation & close
- Pick a tour day and time that works for them.
- Reinforce the {incentive} — it's contingent on attending the full tour.
- State the deposit (see Deposit logic below).
- Read back: day, time, confirmation code, deposit amount.
- Confirm by SMS.

# Deposit logic (branches on guest_stay_type)

- If `guest_stay_type` is `on_property`: "We'll put a seventy-five dollar
  hold on your resort folio. It's removed automatically when you show up
  for the tour."
- If `guest_stay_type` is `off_property`: "We just need to capture a
  seventy-five dollar refundable credit-card deposit to hold the spot.
  You get it back at the tour."

# Behavioral signals (read as you talk)

In addition to the gates, listen for:
- Buyer indicators: signs of disposable income, recent travel, lifestyle
  cues that suggest the tour will convert.
- Decision dynamics: who actually makes the financial decisions in the
  household; whether partners are aligned.
- Travel habits: frequency of resort / cruise vacations, vacation
  ownership interest.
- Personality fit: open-minded vs. resistant; friendly vs. annoyed at
  being called.

These are not pass/fail — they inform tone, pacing, and how hard you
push the close.

# Objection handling

You have a `lookup_objection` tool with the resort's vetted Top 100
rebuttals. ALWAYS call it the first time the caller raises hesitation,
resistance, or pushback (time, money, spouse, trust, prior bad
experience). Speak the returned rebuttal naturally in your own warm
tone, then immediately follow with a soft trial close ("Does morning or
afternoon work better?"). If the tool returns no_match, acknowledge
warmly in one short line and trial-close.

# Tools

- `lookup_objection(objection_text)` — first response to any caller
  pushback.
- `record_answer(question_id, answer, verbatim)` — after every
  qualification answer (yes / no / unclear / refused + verbatim quote).
- `transfer_to_human(reason)` — if the caller asks for a person, OR if
  qualification gets confused and you can't recover.
- `detect_voicemail()` — if the call connects to an answering machine.

# Guardrails

- Never claim to be human.
- Never quote pricing, points, financing terms, APR, or expiration dates
  for any package — Deedy books tours, never sells the product.
- Never imply government or military endorsement.
- Stay within OPC-tour scope. Decline harmful, lawful-but-out-of-scope,
  or unrelated requests.
- Protect privacy. Never read back card numbers, SSNs, or DOBs aloud.

# Goal

Book qualified guests on a ninety-to-one-hundred-twenty minute resort
preview tour. You succeed when the caller has passed all nine gates,
chosen a tour slot, had the seventy-five dollar deposit captured (folio
hold or CC), and heard the confirmation code spoken and texted.
""".strip()


def render_persona(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_GUEST_CONTEXT, **(ctx or {})}
    return PERSONA_INSTRUCTIONS_TEMPLATE.format(**merged)


def render_greeting(ctx: dict[str, str] | None = None) -> str:
    merged = {**DEFAULT_GUEST_CONTEXT, **(ctx or {})}
    return GREETING_INSTRUCTIONS_TEMPLATE.format(**merged)


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
        self._guest_context = {**DEFAULT_GUEST_CONTEXT, **(guest_context or {})}
        super().__init__(instructions=render_persona(self._guest_context))

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
    logger.info(
        "joining room=%s job_id=%s phone=%s resort=%s",
        ctx.room.name,
        ctx.job.id,
        phone_number or "<auto-dispatch>",
        guest_ctx.get("resort_name", DEFAULT_GUEST_CONTEXT["resort_name"]),
    )

    # Wait for the SIP participant to join the room. The orchestrator
    # creates the SIP participant in the same room as our agent. Auto-
    # dispatch means we may join the room slightly before or after the
    # SIP participant — wait_for_participant handles both.
    try:
        if phone_number:
            sip_participant = await ctx.wait_for_participant(identity=phone_number)
        else:
            sip_participant = await ctx.wait_for_participant()
        logger.info("participant joined: identity=%s", sip_participant.identity)
    except Exception as e:
        logger.warning("never saw participant: %s", e)
        ctx.shutdown()
        return

    # ---- mid-call hangup detection ----
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p) -> None:  # type: ignore[no-untyped-def]
        # The agent's own identity is server-generated; we only care
        # about the SIP participant leaving.
        if sip_participant and p.identity != sip_participant.identity:
            return
        reason = getattr(p, "disconnect_reason", None)
        logger.info("caller disconnected reason=%s", reason)

    # STT-LLM-TTS pipeline (replaces xAI realtime model — robotic voice
    # was the trigger for this pivot).
    #
    #   STT: Deepgram Nova-3       — fastest, ~150ms transcription
    #   LLM: Grok-4-fast (non-reasoning) via OpenAI-compatible endpoint
    #   TTS: Rime Arcana           — natural-sounding, telephony-grade
    #   VAD: Silero                 — drives turn-taking + barge-in
    #
    # TTFA budget: ~800–1200ms (vs ~600ms for the realtime model).
    # The latency hit buys dramatically more natural voice quality
    # and ~30–40% lower per-minute cost.
    stt = deepgram.STT(
        model="nova-3",
        language="en-US",
        # Tighter endpointing for snappier turn-taking on the SIP edge.
        endpointing_ms=25,
        smart_format=True,
        # Filler words help the LLM read natural speech ("um, yeah").
        filler_words=True,
    )

    llm = openai.LLM.with_x_ai(
        model="grok-4-fast-non-reasoning",
        api_key=os.environ["XAI_API_KEY"],
        # Lower temperature keeps Deedy on-script. The persona already
        # shapes tone; we don't want creative riffing during a
        # qualification call.
        temperature=0.4,
        parallel_tool_calls=False,
    )

    tts = rime.TTS(
        model="arcana",
        # Luna = warm, friendly female — matches Deedy's concierge
        # persona. Other Arcana options if Luna doesn't land:
        # 'celeste' (calmer), 'sage' (older), 'hank' (male alt).
        speaker="luna",
        lang="eng",
        # Telephony narrowband — agent + Twilio both 8kHz μ-law.
        sample_rate=22050,  # Rime resamples; 22050 is a good middle ground
        reduce_latency=True,
        api_key=os.environ.get("RIME_API_KEY"),
    )

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=silero.VAD.load(),
    )

    # AgentSession.start() in livekit-agents 1.5.7 does NOT accept a
    # `participant=` kwarg — the session picks up audio from whoever
    # is in the room. The participant we just waited for is now
    # speaking into the same room as us, so this just works.
    await session.start(
        agent=VBAQualifierAgent(guest_context=guest_ctx),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # BVCTelephony tuned for 8kHz/narrowband SIP audio.
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    # Speak first AFTER the callee is in the room. For outbound, this
    # avoids the "greeting plays into ringback" trap. The caller
    # consented to an AI call on the QR page and is expecting the
    # disclosure in the first 10 seconds (FCC PEWC requirement).
    await session.generate_reply(instructions=render_greeting(guest_ctx))


def cli_main() -> None:
    """Console entrypoint exposed as `vba-worker`.

    NOTE: agent_name is intentionally OMITTED. With agent_name set, the
    worker only accepts named-dispatch jobs (AgentDispatchClient). On
    LiveKit Cloud, named dispatch routing has been unreliable in our
    setup — dispatches show in `lk dispatch list` but never arrive at
    the worker, leaving the SIP participant in an empty room.

    Without agent_name, the worker AUTO-DISPATCHES into every new room.
    The orchestrator only needs to create the SIP participant; the
    agent joins automatically. This is the documented LiveKit pattern
    for single-agent deployments.

    For Phase 2 multi-tenant routing, switch back to named dispatch
    once we figure out why routing fails (likely stale ghost workers
    in LiveKit Cloud's routing table — fresh project should fix).
    """
    agents.cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


# Alias so `python -m voxaris_agent.worker` works as well as the script.
cli = cli_main


if __name__ == "__main__":
    cli_main()
