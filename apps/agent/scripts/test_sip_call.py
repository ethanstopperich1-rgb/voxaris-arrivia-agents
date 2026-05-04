"""Place a test outbound call by dispatching the agent.

The agent's entrypoint reads phone_number + guest context from job
metadata, creates the SIP participant via the Twilio trunk, waits for
pickup, then starts the session and speaks the greeting. This matches
the LiveKit-recommended outbound pattern (see
docs/livekit/outbound-calls.md) — the orchestrator only dispatches.

PREREQUISITES
- `python -m scripts.setup_sip` has been run (creates the SIP trunk)
- The agent worker is running: `python -m voxaris_agent.worker dev`
  (in another shell — must be registered before we dispatch)

Usage:
    python -m scripts.test_sip_call --to '+1XXXXXXXXXX'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

E164 = re.compile(r"^\+[1-9]\d{6,14}$")


async def place_call(
    to: str,
    resort_name: str,
    incentive: str,
    guest_stay_type: str,
    placement_location: str,
) -> None:
    if not E164.fullmatch(to):
        sys.exit(f"--to must be E.164, got: {to!r}")

    if not os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID"):
        sys.exit("LIVEKIT_SIP_OUTBOUND_TRUNK_ID missing — run setup_sip first")

    room_name = f"vba-test-{secrets.token_hex(4)}"
    consent_id = secrets.token_hex(8)

    metadata = json.dumps(
        {
            "phone_number": to,
            "consent_id": consent_id,
            "resort_name": resort_name,
            "incentive": incentive,
            "guest_stay_type": guest_stay_type,
            "placement_location": placement_location,
        }
    )

    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    try:
        # 1. Pre-create the room.
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=120,
                # Pass guest context as room metadata so the auto-
                # dispatched agent can read it from ctx.room.metadata.
                metadata=metadata,
            ),
        )
        print(f"  room:       {room_name}")

        # 2. Create the SIP participant. The agent worker is in
        # auto-dispatch mode — it joins every new room automatically,
        # no AgentDispatch needed.
        trunk_id = os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]
        # Pass sip_number per-call (caller-ID) since the trunk uses
        # numbers=["*"] now — the +1407 DID is no longer pinned to
        # the Twilio trunk (it had to be unbound so inbound calls
        # could fall through to the Voice URL).
        sip = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=to,
                sip_number=os.environ.get("TWILIO_VOICE_NUMBER", "+14072890294"),
                room_name=room_name,
                participant_identity=to,
                participant_name="Caller",
                krisp_enabled=True,
                wait_until_answered=False,
            )
        )
        print(f"  SIP call:   {sip.sip_call_id}")
        print(f"  dialing:    {to} — answer your phone")
    finally:
        await lk.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--to", required=True, help="E.164 phone number, e.g. +14155551234")
    p.add_argument("--resort", default="Westgate Resorts")
    p.add_argument(
        "--incentive", default="complimentary three-night Orlando getaway"
    )
    p.add_argument(
        "--stay", choices=["on_property", "off_property"], default="off_property"
    )
    p.add_argument("--placement", default="kiosk")
    args = p.parse_args()
    asyncio.run(
        place_call(
            args.to, args.resort, args.incentive, args.stay, args.placement
        )
    )


if __name__ == "__main__":
    main()
