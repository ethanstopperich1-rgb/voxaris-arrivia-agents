"""Place a test outbound call from LiveKit through the Twilio SIP trunk
and dispatch the `vba-qualifier` agent into the room to greet the caller.

PREREQUISITES
- `python -m scripts.setup_sip` has been run (creates the SIP trunk)
- The agent worker is running: `python -m voxaris_agent.worker dev`
  (in another shell — it must be registered before we dispatch)

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


async def place_call(to: str) -> None:
    if not E164.fullmatch(to):
        sys.exit(f"--to must be E.164, got: {to!r}")

    trunk_id = os.environ.get("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
    if not trunk_id:
        sys.exit("LIVEKIT_SIP_OUTBOUND_TRUNK_ID missing — run setup_sip first")

    room_name = f"vba-test-{secrets.token_hex(4)}"
    consent_id = secrets.token_hex(8)

    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    try:
        # 1. Create the room (eager, so dispatch finds it).
        await lk.room.create_room(
            api.CreateRoomRequest(name=room_name, empty_timeout=120),
        )
        print(f"  room: {room_name}")

        # 2. Dispatch the vba-qualifier agent into the room.
        metadata = json.dumps({"consent_id": consent_id, "phone_e164": to})
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="vba-qualifier",
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"  agent dispatched: {dispatch.id}")

        # 3. Create the SIP participant — Twilio dials the PSTN.
        sip = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=to,
                room_name=room_name,
                participant_identity="caller",
                participant_name="Test Caller",
                krisp_enabled=True,
                wait_until_answered=True,
            )
        )
        print(f"  SIP participant: {sip.participant_id}")
        print(f"  call placed to {to} — answer your phone")
        print()
        print(f"  watch worker logs for greeting + transcription")
        print(f"  (room {room_name} auto-cleans after 120s empty)")
    finally:
        await lk.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--to", required=True, help="E.164 phone number, e.g. +14155551234")
    args = p.parse_args()
    asyncio.run(place_call(args.to))


if __name__ == "__main__":
    main()
