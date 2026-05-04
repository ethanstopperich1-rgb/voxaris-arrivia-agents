"""Wire inbound calls: PSTN → Twilio → TwiML Bin → LiveKit SIP → agent.

Twilio Elastic SIP Trunking does NOT support username/password auth on
INBOUND, so we use Twilio Programmable Voice + a TwiML Bin to dial
into LiveKit's SIP host. LiveKit's inbound trunk authenticates the
INVITE with the same SIP credential we set up for outbound.

Idempotent — re-runs reuse existing resources.

What this does
--------------
1. Confirm the LiveKit SIP host (inferred from LIVEKIT_URL).
2. Twilio side:
   a. Create a TwiML Bin with <Dial><Sip username/password> → LiveKit.
   b. Configure the +1407 DID's Voice URL to use the TwiML Bin.
3. LiveKit side:
   a. Create an inbound trunk for the DID with matching auth creds.
   b. Create an individual dispatch rule (one room per caller).
      Our worker is in auto-dispatch mode so it joins automatically.

Run:
    python -m scripts.setup_inbound
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from livekit import api

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def envreq(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing required env var: {name}")
    return v


def upsert_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"  .env: {key} updated")


def lk_sip_host() -> str:
    url = envreq("LIVEKIT_URL")
    project_id = url.replace("wss://", "").replace("https://", "").split(".")[0]
    return f"{project_id}.sip.livekit.cloud"


class Twilio:
    def __init__(self) -> None:
        self.account_sid = envreq("TWILIO_ACCOUNT_SID")
        self.client = httpx.Client(
            auth=(envreq("TWILIO_API_KEY_SID"), envreq("TWILIO_API_KEY_SECRET")),
            timeout=30.0,
        )
        self.voice_number = envreq("TWILIO_VOICE_NUMBER")

    def _root(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    def find_twiml_bin(self, friendly_name: str) -> tuple[str, str] | None:
        """Returns (sid, url) for an existing TwiML Bin matching name."""
        # TwiML Bins live under FriendlyNames in the bins endpoint
        r = self.client.get(
            f"https://serverless-upload.twilio.com/v1/Bins"
            if False else
            f"{self._root()}/Applications.json"
        )
        # Actually TwiML Bins use a different API; for simplicity use Applications
        # But for THIS use case, we'll use a Twilio Function or just inline TwiML
        # via the IncomingPhoneNumber's VoiceUrl pointing at a static TwiML.
        # Simplest: use Twilio's "TwiML Apps" (Applications) which supports
        # VoiceUrl with inline TwiML hosted by Twilio.
        return None

    def find_or_create_twiml_app(
        self, friendly_name: str, twiml_url: str
    ) -> str:
        """Find or create a Twilio Application with VoiceUrl set."""
        r = self.client.get(f"{self._root()}/Applications.json")
        r.raise_for_status()
        for a in r.json().get("applications", []):
            if a.get("friendly_name") == friendly_name:
                print(f"  twilio: app '{friendly_name}' exists ({a['sid']})")
                # Update VoiceUrl in case it changed
                self.client.post(
                    f"{self._root()}/Applications/{a['sid']}.json",
                    data={"VoiceUrl": twiml_url, "VoiceMethod": "POST"},
                )
                return a["sid"]
        r2 = self.client.post(
            f"{self._root()}/Applications.json",
            data={
                "FriendlyName": friendly_name,
                "VoiceUrl": twiml_url,
                "VoiceMethod": "POST",
            },
        )
        r2.raise_for_status()
        sid = r2.json()["sid"]
        print(f"  twilio: created app {sid}")
        return sid

    def assign_number_to_app(self, app_sid: str) -> None:
        """Point the DID's Voice routing at the Application."""
        r = self.client.get(
            f"{self._root()}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": self.voice_number},
        )
        r.raise_for_status()
        nums = r.json().get("incoming_phone_numbers", [])
        if not nums:
            sys.exit(f"phone number {self.voice_number} not found on account")
        pn_sid = nums[0]["sid"]
        if nums[0].get("voice_application_sid") == app_sid:
            print(f"  twilio: {self.voice_number} already routed to app")
            return
        r2 = self.client.post(
            f"{self._root()}/IncomingPhoneNumbers/{pn_sid}.json",
            data={"VoiceApplicationSid": app_sid, "VoiceUrl": ""},
        )
        r2.raise_for_status()
        print(f"  twilio: {self.voice_number} → app {app_sid}")


async def setup_livekit_inbound(
    voice_number: str, sip_user: str, sip_pass: str
) -> str:
    """Create or reuse a LiveKit inbound trunk for the DID."""
    lk = api.LiveKitAPI(
        url=envreq("LIVEKIT_URL"),
        api_key=envreq("LIVEKIT_API_KEY"),
        api_secret=envreq("LIVEKIT_API_SECRET"),
    )
    try:
        existing = await lk.sip.list_sip_inbound_trunk(
            api.ListSIPInboundTrunkRequest()
        )
        for t in existing.items:
            if t.name == "voxaris-vba-twilio-inbound":
                print(f"  livekit: inbound trunk exists ({t.sip_trunk_id})")
                return t.sip_trunk_id

        from livekit.protocol.sip import SIPInboundTrunkInfo

        trunk = SIPInboundTrunkInfo(
            name="voxaris-vba-twilio-inbound",
            numbers=[voice_number],
            auth_username=sip_user,
            auth_password=sip_pass,
            krisp_enabled=True,
        )
        resp = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(trunk=trunk)
        )
        print(f"  livekit: created inbound trunk {resp.sip_trunk_id}")
        return resp.sip_trunk_id
    finally:
        await lk.aclose()


async def setup_dispatch_rule(trunk_id: str) -> str:
    """Create an individual dispatch rule — one room per caller. Our
    worker is in auto-dispatch mode, so it joins automatically."""
    lk = api.LiveKitAPI(
        url=envreq("LIVEKIT_URL"),
        api_key=envreq("LIVEKIT_API_KEY"),
        api_secret=envreq("LIVEKIT_API_SECRET"),
    )
    try:
        existing = await lk.sip.list_sip_dispatch_rule(
            api.ListSIPDispatchRuleRequest()
        )
        for r in existing.items:
            if r.name == "voxaris-vba-inbound":
                print(f"  livekit: dispatch rule exists ({r.sip_dispatch_rule_id})")
                return r.sip_dispatch_rule_id

        # Same persona context as outbound. The agent's
        # DEFAULT_GUEST_CONTEXT already covers everything; the room
        # metadata makes "direction=inbound" available for analytics
        # and lets the agent know it's an inbound call.
        import json

        room_meta = json.dumps({
            "direction": "inbound",
            "property_name": "Westgate Lakes Resort & Spa",
            "premium_offer": "two complimentary 2-day Disney park hopper tickets",
            "placement_name": "QR scan",
        })

        rule = api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                room_prefix="inbound-",
            )
        )
        info = api.SIPDispatchRuleInfo(
            rule=rule,
            name="voxaris-vba-inbound",
            trunk_ids=[trunk_id],
            metadata=room_meta,
        )
        resp = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(dispatch_rule=info)
        )
        print(f"  livekit: created dispatch rule {resp.sip_dispatch_rule_id}")
        return resp.sip_dispatch_rule_id
    finally:
        await lk.aclose()


def main() -> None:
    print("Voxaris VBA — Inbound setup")
    print()

    sip_host = lk_sip_host()
    voice_number = envreq("TWILIO_VOICE_NUMBER")
    sip_user = envreq("LIVEKIT_SIP_USERNAME")
    sip_pass = envreq("LIVEKIT_SIP_PASSWORD")

    print(f"  LiveKit SIP host: {sip_host}")
    print(f"  Twilio DID:       {voice_number}")
    print(f"  SIP credentials:  {sip_user} / [hidden, {len(sip_pass)} chars]")
    print()

    # The TwiML that Twilio serves when the DID rings:
    #   <Dial><Sip username="..." password="...">
    #     sip:+1407...@voxaris-vba-ks6ggp0s.sip.livekit.cloud;transport=tcp
    #   </Sip></Dial>
    #
    # We host this as a TwiML Bin via the Twilio Applications API. The
    # Applications endpoint accepts an inline-TwiML URL via Twilio's
    # "TwiML Bins" service. Simplest: use https://handler.twilio.com
    # for inline TwiML hosting? No — that's Twilio Functions. The
    # cleanest path is to host the TwiML on our own domain.
    #
    # For now we'll use the TwiML Bin approach via the dashboard URL
    # pattern. Easier: have the user paste the TwiML into Twilio's
    # TwiML Bin UI manually, OR have a tiny Vercel endpoint return it.
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip username="{sip_user}" password="{sip_pass}">
      sip:{voice_number}@{sip_host};transport=tcp
    </Sip>
  </Dial>
</Response>""".strip()

    print("[1/3] LiveKit inbound trunk")
    trunk_id = asyncio.run(setup_livekit_inbound(voice_number, sip_user, sip_pass))
    upsert_env("LIVEKIT_SIP_INBOUND_TRUNK_ID", trunk_id)

    print()
    print("[2/3] LiveKit dispatch rule")
    rule_id = asyncio.run(setup_dispatch_rule(trunk_id))
    upsert_env("LIVEKIT_SIP_DISPATCH_RULE_ID", rule_id)

    print()
    print("[3/3] Twilio TwiML Bin — MANUAL STEP")
    print()
    print("  The Twilio REST API doesn't expose TwiML Bin creation directly,")
    print("  so paste the following into the Twilio Console:")
    print()
    print("  https://console.twilio.com/us1/develop/twiml-bins/twiml-bins")
    print("  → Create new TwiML Bin")
    print("  → Friendly name: 'voxaris-vba-inbound'")
    print("  → TwiML body:")
    print()
    print("------ COPY BELOW ------")
    print(twiml)
    print("------ COPY ABOVE ------")
    print()
    print("  Then go to:")
    print("  https://console.twilio.com/us1/develop/phone-numbers/manage/incoming")
    print(f"  → click {voice_number}")
    print("  → A Call Comes In: select 'TwiML Bin' → 'voxaris-vba-inbound'")
    print("  → Save")
    print()
    print("Once that's saved, calls TO +14072890294 will route into LiveKit")
    print("and our worker (auto-dispatch) will pick them up automatically.")


if __name__ == "__main__":
    main()
