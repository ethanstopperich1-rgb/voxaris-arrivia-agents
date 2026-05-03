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
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="vba-qualifier",
                room=room_name,
                metadata=metadata,
            )
        )
        print(f"  dispatched: {dispatch.id}")
        print(f"  room:       {room_name}")
        print(f"  dialing:    {to}")
        print()
        print("  watch worker logs — agent creates the SIP participant,")
        print("  waits until you answer, then speaks the greeting.")
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
