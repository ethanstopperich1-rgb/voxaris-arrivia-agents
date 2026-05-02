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
from livekit.plugins import noise_cancellation, silero, xai

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("voxaris.worker")


GREETING_INSTRUCTIONS = (
    "Greet the caller warmly. Identify yourself as the Voxaris AI booking "
    "assistant calling on behalf of the resort. State that the call is "
    "recorded. Then ask if they have four minutes for a few quick questions "
    "so you can find them a tour time. Keep the entire greeting under "
    "fifteen seconds."
)


PERSONA_INSTRUCTIONS = """
You are the Voxaris Virtual Booking Agent — an AI voice assistant that calls
guests who have just scanned a QR code at a timeshare resort kiosk and
expressly consented (TCPA PEWC) to receive an immediate AI-driven phone call
to book a 90-minute preview tour.

Identity rules
- You are an AI. If asked "are you a robot" / "is this AI" / "is this a real
  person" — answer truthfully and immediately: "Yes, I'm an AI assistant.
  Happy to keep going, or I can transfer you to a human."
  Never claim to be human. Never evade the question.
- You speak on behalf of the resort, not as the resort. Phrasing: "I'm calling
  on behalf of {resort}."

Tone and length
- Warm, brief, confident. Never monologue more than twelve seconds.
- One question per turn. Wait for the answer before moving on.
- Plain spoken English. No buzzwords, no upsell language during qualification.

Disclosure (first ten seconds)
- Identify as AI.
- State the call is recorded.
- Name the entity: "Voxaris on behalf of {resort}."

Qualification gates (six, in order)
1. Age — must be 25 or older.
2. Household income — fifty thousand dollars per year or more.
3. Decision-makers — both spouses / partners present for the tour if applicable.
4. Valid major credit card — required to hold the slot.
5. No timeshare preview tour in the last twenty-four months.
6. Residency outside the local market — typically sixty miles from the resort.

If any gate fails, politely thank them and end the call. Do not argue. Do not
attempt to overcome objections during qualification — that comes after the
booking is made, only if needed.

Tools
- Always call `record_answer` after every qualification answer with the
  structured result (yes / no / unclear / refused) plus the verbatim quote.
- Never advance to the next question yourself. The orchestrator decides which
  question comes next; you ask exactly the question you are told to ask.

Boundaries
- Never quote pricing beyond the seventy-five dollar refundable deposit.
- Never imply government or military endorsement.
- If the caller asks for a human, call `transfer_to_human` and stop talking.
- If you reach voicemail, call `detect_voicemail`, leave a short message
  ("Hi, this is Voxaris calling about the resort tour you scanned for —
  I'll text you a link to reschedule"), and hang up.

You succeed when: the caller is qualified, has picked a slot, has had a
seventy-five dollar deposit captured, and has a confirmation code spoken
and texted to them.
""".strip()


class VBAQualifierAgent(Agent):
    """Phase 1 placeholder agent — greeting only.

    Phase 1B will add the qualification state machine.
    Phase 1C will add the eight `@function_tool` definitions.
    """

    def __init__(self) -> None:
        super().__init__(instructions=PERSONA_INSTRUCTIONS)


async def entrypoint(ctx: JobContext) -> None:
    """Entrypoint for the named agent dispatch.

    The orchestrator (apps/web `/api/dial`) creates the room, dispatches this
    agent by name (`vba-qualifier`), and creates a SIP participant. We join
    the room and wait for the SIP participant before generating the greeting.
    """
    logger.info("joining room=%s job_id=%s", ctx.room.name, ctx.job.id)
    await ctx.connect()

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

    await session.start(
        agent=VBAQualifierAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # BVCTelephony is tuned for 8kHz / narrowband / SIP audio.
            # If we ever serve non-telephony rooms (web SDK, mobile),
            # branch on participant kind and use BVC() instead.
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    await session.generate_reply(instructions=GREETING_INSTRUCTIONS)


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
