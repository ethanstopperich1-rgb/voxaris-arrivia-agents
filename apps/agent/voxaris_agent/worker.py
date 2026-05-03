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
from livekit.plugins import noise_cancellation, silero, xai

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
    "Greet the caller warmly. Introduce yourself as Deedy, the Voxaris AI "
    "booking assistant calling on behalf of {resort_name}. Clearly state the "
    "call is recorded. Then ask: \"Do you have about four minutes for a few "
    "quick questions so I can see if you qualify for the {incentive} and "
    "find you a good tour time?\" "
    "Keep the entire greeting under fifteen seconds."
)


PERSONA_INSTRUCTIONS_TEMPLATE = """
You are Deedy, the Voxaris Virtual Booking Agent — a warm, friendly AI voice
assistant that calls guests who have just scanned a QR code at a timeshare
resort and expressly consented (TCPA PEWC) to receive this immediate
AI-driven call.

Identity rules
- You are an AI. If asked "are you a robot", "is this AI", "is this a real
  person", or anything similar — answer truthfully and immediately:
  "Yes, I'm Deedy, an AI assistant calling on behalf of {resort_name}.
  Happy to keep going, or I can transfer you to a human."
- Never claim to be human. Never evade the question.
- You speak on behalf of the resort. Always use:
  "I'm calling on behalf of {resort_name}."

Tone and length
- Warm, brief, confident, and conversational — like a helpful resort
  concierge.
- Never monologue more than twelve seconds.
- One clear question per turn. Wait for the answer before moving on.
- Plain spoken English. No buzzwords, no pressure, no upsell language during
  qualification.

Disclosure (first ten seconds)
- Identify yourself as Deedy, an AI assistant.
- State the call is recorded.
- Name the entity:
  "This is Deedy from Voxaris calling on behalf of {resort_name}."

=== CURRENT GUEST CONTEXT (injected dynamically) ===
- Resort name: {resort_name}
- Incentive / offer: {incentive}
- Guest stay type: {guest_stay_type}
- Placement location: {placement_location}

Qualification gates (ask exactly in this order)
1. Age — must be 25 or older and legally able to enter a contract.
2. Combined household income — fifty thousand dollars per year or more.
3. Decision-makers — both spouses/partners must attend the tour together if
   applicable.
4. Valid major credit card (not prepaid) — required to hold the slot.
5. No timeshare preview tour in the last twenty-four months.
6. Residency — lives outside the local marketing area (typically more than
   sixty miles from the resort).

If any gate fails, politely thank them, explain they don't qualify for the
offer, and end the call warmly. Do not argue or try to overcome qualification
gates.

Deposit logic
- If guest_stay_type is on_property: "We'll just put a $75 hold on your
  resort folio — it's removed when you show up."
- If guest_stay_type is off_property: Collect a $75 refundable credit card
  deposit to secure the spot.

Objection handling
You have full access to the Top 100 Objections guide. When an objection
appears, respond with the appropriate rebuttal from that guide and
immediately follow with a soft trial close.

Tools
- Use `record_answer` after every qualification answer
  (yes / no / unclear / refused + verbatim quote).
- Use `transfer_to_human` if the caller requests a human.
- Use `detect_voicemail` if you reach voicemail.

You succeed when the caller is fully qualified, has chosen a tour slot, the
deposit is handled, and they have a confirmation code spoken and texted.
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
    guest_ctx = parse_metadata(ctx.job.metadata)
    phone_number = guest_ctx.get("phone_number")
    is_outbound = phone_number is not None
    logger.info(
        "joining room=%s job_id=%s direction=%s resort=%s stay=%s",
        ctx.room.name,
        ctx.job.id,
        "outbound" if is_outbound else "inbound",
        guest_ctx.get("resort_name", DEFAULT_GUEST_CONTEXT["resort_name"]),
        guest_ctx.get("guest_stay_type", DEFAULT_GUEST_CONTEXT["guest_stay_type"]),
    )
    await ctx.connect()

    # ---- OUTBOUND: place the call ourselves and wait for pickup ----
    sip_participant = None
    if is_outbound:
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
                    participant_identity=phone_number,
                    participant_name="Caller",
                    krisp_enabled=True,
                    # Block until the callee actually picks up. If they
                    # reject / don't answer / the trunk fails, this
                    # raises TwirpError with sip_status_code metadata.
                    wait_until_answered=True,
                )
            )
            logger.info("call answered: %s", phone_number)
        except api.TwirpError as e:
            sip_code = e.metadata.get("sip_status_code") if e.metadata else None
            sip_status = e.metadata.get("sip_status") if e.metadata else None
            logger.warning(
                "call did not connect: %s (SIP %s %s)",
                e.message,
                sip_code,
                sip_status,
            )
            ctx.shutdown()
            return

        # Wait for the SIP participant to fully join the room before
        # starting the session — otherwise the greeting plays before
        # they're listening.
        sip_participant = await ctx.wait_for_participant(identity=phone_number)

    # ---- mid-call hangup detection ----
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p) -> None:  # type: ignore[no-untyped-def]
        # The agent's own identity is server-generated; we only care
        # about the SIP participant leaving.
        if sip_participant and p.identity != sip_participant.identity:
            return
        reason = getattr(p, "disconnect_reason", None)
        logger.info("caller disconnected reason=%s", reason)

    # livekit-plugins-xai 1.5.7 surface (verified by introspection):
    #   - voice literal is PascalCase: 'Ara' | 'Eve' | 'Leo' | 'Rex' | 'Sal'
    #     (xAI docs show lowercase but the plugin uses Pascal).
    #   - turn_detection is openai.types.beta.realtime.TurnDetection
    #     (TypedDict): type, threshold, silence_duration_ms,
    #     prefix_padding_ms, eagerness, interrupt_response,
    #     create_response.
    #   - NO input_audio_format / output_audio_format kwargs are
    #     exposed on the constructor or on update_options(). The
    #     plugin abstracts audio negotiation at the LiveKit pipeline
    #     level and uses PCM internally, so we lose the μ-law-passthrough
    #     latency optimization the build plan assumed. Acceptable for
    #     MVP. If Day 2 PM TTFA stays above 1.5s, options:
    #     (a) PR livekit-plugins-xai to expose audio config, or
    #     (b) drop the plugin and write a direct xAI WebSocket client
    #         per the xai-cookbook telephony example.
    llm = xai.realtime.RealtimeModel(
        model="grok-voice-think-fast-1.0",
        voice="Eve",
        api_key=os.environ["XAI_API_KEY"],
        turn_detection={
            "type": "server_vad",
            "silence_duration_ms": 600,
        },
        # Hard cap below xAI's 30-min session limit so we always
        # close cleanly before the server tears us down.
        max_session_duration=25 * 60,
    )

    session = AgentSession(
        llm=llm,
        # Silero gives us a room-level "user is speaking" indicator on
        # the dashboard; xAI server VAD still drives turn-taking inside
        # the realtime session.
        vad=silero.VAD.load(),
    )

    start_kwargs: dict = {
        "agent": VBAQualifierAgent(guest_context=guest_ctx),
        "room": ctx.room,
        "room_input_options": RoomInputOptions(
            # BVCTelephony is tuned for 8kHz / narrowband / SIP audio.
            # If we ever serve non-telephony rooms (web SDK, mobile),
            # branch on participant kind and use BVC() instead.
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    }
    # For outbound, bind the session to the SIP participant we just
    # added so audio flows directly to/from them. For inbound, the
    # SIP participant is in the room already (added by the dispatch
    # rule); session picks them up automatically.
    if sip_participant is not None:
        start_kwargs["participant"] = sip_participant

    await session.start(**start_kwargs)

    # Speak first AFTER the callee is in the room. For outbound, this
    # avoids the "greeting plays into ringback" trap. The caller
    # consented to an AI call on the QR page and is expecting the
    # disclosure in the first 10 seconds (FCC PEWC requirement).
    await session.generate_reply(instructions=render_greeting(guest_ctx))


def cli_main() -> None:
    """Console entrypoint exposed as `vba-worker`."""
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # Named-agent dispatch only. Web orchestrator must explicitly
            # dispatch via AgentDispatchClient — no auto-dispatch on room
            # creation.
            agent_name="vba-qualifier",
        )
    )


# Alias so `python -m voxaris_agent.worker` works as well as the script.
cli = cli_main


if __name__ == "__main__":
    cli_main()
