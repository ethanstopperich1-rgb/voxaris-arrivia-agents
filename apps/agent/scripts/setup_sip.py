"""One-shot LiveKit ↔ Twilio Elastic SIP Trunking setup.

Idempotent: re-runs are safe — existing resources are reused, not duplicated.
Writes the resulting trunk IDs back into apps/agent/.env so the agent worker
and the web orchestrator can both read them.

What this does
--------------
1. Generate a long random SIP credential (username + password).
2. Twilio side:
   a. Create a Credential List "voxaris-vba-livekit-creds".
   b. Add the credential to the list.
   c. Attach the credential list to the Twilio Elastic SIP Trunk.
   d. Assign the Twilio DID (+1407...) to the trunk so outbound calls
      use it as caller-ID.
3. LiveKit side:
   a. Create an outbound SIP trunk that POSTs INVITEs to the Twilio
      termination URI using those SIP credentials.
4. Persist the LiveKit outbound-trunk ID + the SIP password to .env.

Run:
    python -m scripts.setup_sip
"""

from __future__ import annotations

import asyncio
import os
import secrets
import string
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
    """Idempotently set KEY=VALUE in apps/agent/.env."""
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


def gen_password(length: int = 40) -> str:
    """Long random password — Twilio rejects weak ones with 21610."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Twilio:
    """Twilio Elastic SIP Trunking REST helpers (using API Key auth)."""

    def __init__(self) -> None:
        self.account_sid = envreq("TWILIO_ACCOUNT_SID")
        self.client = httpx.Client(
            auth=(envreq("TWILIO_API_KEY_SID"), envreq("TWILIO_API_KEY_SECRET")),
            timeout=30.0,
        )
        self.trunk_sid = envreq("TWILIO_TRUNK_SID")
        self.voice_number = envreq("TWILIO_VOICE_NUMBER")

    def _api_root(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    def find_credential_list(self, name: str) -> str | None:
        r = self.client.get(f"{self._api_root()}/SIP/CredentialLists.json")
        r.raise_for_status()
        for cl in r.json().get("credential_lists", []):
            if cl.get("friendly_name") == name:
                return cl["sid"]
        return None

    def create_credential_list(self, name: str) -> str:
        r = self.client.post(
            f"{self._api_root()}/SIP/CredentialLists.json",
            data={"FriendlyName": name},
        )
        r.raise_for_status()
        return r.json()["sid"]

    def add_credential(self, list_sid: str, username: str, password: str) -> str:
        r = self.client.post(
            f"{self._api_root()}/SIP/CredentialLists/{list_sid}/Credentials.json",
            data={"Username": username, "Password": password},
        )
        if r.status_code == 409 or "already exists" in r.text.lower():
            # Look up existing
            r2 = self.client.get(
                f"{self._api_root()}/SIP/CredentialLists/{list_sid}/Credentials.json"
            )
            r2.raise_for_status()
            for c in r2.json().get("credentials", []):
                if c["username"] == username:
                    print(f"  twilio: credential '{username}' already exists, reusing")
                    return c["sid"]
        r.raise_for_status()
        return r.json()["sid"]

    def attach_cred_list_to_trunk(self, list_sid: str) -> None:
        r = self.client.get(
            f"https://trunking.twilio.com/v1/Trunks/{self.trunk_sid}/CredentialLists"
        )
        r.raise_for_status()
        for cl in r.json().get("credential_lists", []):
            if cl["sid"] == list_sid:
                print("  twilio: cred list already attached to trunk")
                return
        r2 = self.client.post(
            f"https://trunking.twilio.com/v1/Trunks/{self.trunk_sid}/CredentialLists",
            data={"CredentialListSid": list_sid},
        )
        r2.raise_for_status()
        print("  twilio: cred list attached to trunk")

    def assign_number_to_trunk(self) -> None:
        # Find the IncomingPhoneNumber SID for our DID
        r = self.client.get(
            f"{self._api_root()}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": self.voice_number},
        )
        r.raise_for_status()
        nums = r.json().get("incoming_phone_numbers", [])
        if not nums:
            sys.exit(f"phone number {self.voice_number} not found on account")
        pn_sid = nums[0]["sid"]
        # Check if already attached to this trunk
        r2 = self.client.get(
            f"https://trunking.twilio.com/v1/Trunks/{self.trunk_sid}/PhoneNumbers"
        )
        r2.raise_for_status()
        for pn in r2.json().get("phone_numbers", []):
            if pn["phone_number"] == self.voice_number:
                print(f"  twilio: {self.voice_number} already assigned to trunk")
                return
        r3 = self.client.post(
            f"https://trunking.twilio.com/v1/Trunks/{self.trunk_sid}/PhoneNumbers",
            data={"PhoneNumberSid": pn_sid},
        )
        r3.raise_for_status()
        print(f"  twilio: {self.voice_number} assigned to trunk")


async def setup_livekit_outbound(
    twilio_domain: str, sip_user: str, sip_pass: str, voice_number: str
) -> str:
    """Create (or reuse) a LiveKit outbound SIP trunk."""
    livekit_api = api.LiveKitAPI(
        url=envreq("LIVEKIT_URL"),
        api_key=envreq("LIVEKIT_API_KEY"),
        api_secret=envreq("LIVEKIT_API_SECRET"),
    )
    try:
        existing = await livekit_api.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest()
        )
        for t in existing.items:
            if t.name == "voxaris-vba-twilio-outbound":
                print(f"  livekit: outbound trunk already exists ({t.sip_trunk_id})")
                return t.sip_trunk_id

        req = api.CreateSIPOutboundTrunkRequest(
            trunk=api.SIPOutboundTrunkInfo(
                name="voxaris-vba-twilio-outbound",
                address=twilio_domain,
                transport=api.SIPTransport.SIP_TRANSPORT_AUTO,
                numbers=[voice_number],
                auth_username=sip_user,
                auth_password=sip_pass,
            )
        )
        resp = await livekit_api.sip.create_outbound_trunk(req)
        print(f"  livekit: created outbound trunk {resp.sip_trunk_id}")
        return resp.sip_trunk_id
    finally:
        await livekit_api.aclose()


def main() -> None:
    print("Voxaris VBA — SIP setup")
    print()

    # 1. Twilio side
    print("[1/2] Twilio Elastic SIP Trunking")
    tw = Twilio()
    list_name = "voxaris-vba-livekit-creds"
    list_sid = tw.find_credential_list(list_name)
    if list_sid:
        print(f"  twilio: credential list '{list_name}' already exists ({list_sid})")
    else:
        list_sid = tw.create_credential_list(list_name)
        print(f"  twilio: created credential list {list_sid}")

    sip_user = "voxaris-livekit"
    existing_pw = os.environ.get("LIVEKIT_SIP_PASSWORD")
    sip_pass = existing_pw or gen_password()
    tw.add_credential(list_sid, sip_user, sip_pass)
    tw.attach_cred_list_to_trunk(list_sid)
    tw.assign_number_to_trunk()

    if not existing_pw:
        upsert_env("LIVEKIT_SIP_PASSWORD", sip_pass)
    upsert_env("LIVEKIT_SIP_USERNAME", sip_user)
    upsert_env("TWILIO_SIP_CREDENTIAL_LIST_SID", list_sid)

    # 2. LiveKit side
    print()
    print("[2/2] LiveKit outbound SIP trunk")
    twilio_domain = envreq("TWILIO_TRUNK_DOMAIN")
    voice_number = envreq("TWILIO_VOICE_NUMBER")
    trunk_id = asyncio.run(
        setup_livekit_outbound(twilio_domain, sip_user, sip_pass, voice_number)
    )
    upsert_env("LIVEKIT_SIP_OUTBOUND_TRUNK_ID", trunk_id)

    print()
    print("done. To make a test call:")
    print()
    print("  cd apps/agent && source .venv/bin/activate")
    print("  python -m scripts.test_sip_call --to '+1YOURPHONE'")


if __name__ == "__main__":
    main()
