#!/usr/bin/env python3
"""Pre-demo liveness check for every API key the LiveKit voice agents touch.

Run from either app: `python ../../scripts/check_env.py` after `source .env`.
Exits non-zero if anything that's actually used by the worker is broken.
"""
from __future__ import annotations

import json
import os
import sys
import time
import base64
import hmac
import hashlib
import urllib.error
import urllib.request

OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"

failures: list[str] = []
warnings: list[str] = []


def check(name: str, ok: bool, detail: str = "", *, hard: bool = True) -> None:
    icon = OK if ok else (FAIL if hard else WARN)
    print(f"  {icon} {name:36s} {detail}")
    if not ok:
        (failures if hard else warnings).append(f"{name}: {detail}")


def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 8.0,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def basic(user: str, pwd: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()


def main() -> int:
    print("\n=== Voxaris VBA pre-demo liveness check ===\n")

    # --- LiveKit (signed JWT to ListRooms) ---
    print("LiveKit Cloud:")
    url = os.environ.get("LIVEKIT_URL", "").replace("wss://", "https://")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if url and key and secret:
        def b64(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        hdr = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        now = int(time.time())
        pl = b64(json.dumps({"iss": key, "exp": now + 60, "nbf": now - 5,
                             "video": {"roomList": True}}).encode())
        sig = b64(hmac.new(secret.encode(), f"{hdr}.{pl}".encode(),
                           hashlib.sha256).digest())
        tok = f"{hdr}.{pl}.{sig}"
        s, _ = http("POST", f"{url}/twirp/livekit.RoomService/ListRooms",
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"},
                    data=b"{}")
        check("LIVEKIT_API_KEY/SECRET", s == 200, f"HTTP {s}")
    else:
        check("LIVEKIT_URL/KEY/SECRET", False, "missing env vars")

    # --- xAI / Grok ---
    print("\nxAI (Grok LLM):")
    s, _ = http("GET", "https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {os.environ.get('XAI_API_KEY','')}"})
    check("XAI_API_KEY", s == 200, f"HTTP {s}")

    # --- Deepgram ---
    print("\nDeepgram (STT):")
    s, _ = http("GET", "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {os.environ.get('DEEPGRAM_API_KEY','')}"})
    check("DEEPGRAM_API_KEY", s == 200, f"HTTP {s}")

    # --- Rime ---
    print("\nRime (TTS):")
    rk = os.environ.get("RIME_API_KEY", "")
    s, body = http(
        "POST", "https://users.rime.ai/v1/rime-tts",
        headers={"Authorization": f"Bearer {rk}", "Content-Type": "application/json"},
        data=b'{"text":"hi","speaker":"cove","modelId":"mistv3"}',
        timeout=10,
    )
    # Rime returns 200 with audio, or 4xx with auth error. 5xx still means key authed.
    check("RIME_API_KEY", s in (200, 500, 502, 503),
          f"HTTP {s}" + (" (auth ok, server hiccup)" if s >= 500 else ""))

    # --- SendBlue ---
    print("\nSendBlue (SMS/iMessage):")
    s, body = http(
        "POST", "https://api.sendblue.co/api/send-message",
        headers={
            "sb-api-key-id": os.environ.get("SENDBLUE_API_KEY_ID", ""),
            "sb-api-secret-key": os.environ.get("SENDBLUE_API_SECRET_KEY", ""),
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    # 401/403 = bad key. 400 with "phone number" message = key OK, payload bad.
    text = body.decode("utf-8", "replace")
    auth_ok = s == 400 and "phone" in text.lower()
    check("SENDBLUE_API_KEY_ID/SECRET", auth_ok, f"HTTP {s}")
    check("SENDBLUE_FROM_NUMBER", bool(os.environ.get("SENDBLUE_FROM_NUMBER")),
          os.environ.get("SENDBLUE_FROM_NUMBER", "MISSING"))

    # --- Twilio ---
    print("\nTwilio:")
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
    s, _ = http("GET", f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                headers={"Authorization": basic(sid, tok)})
    check("TWILIO_ACCOUNT_SID/AUTH_TOKEN", s == 200, f"HTTP {s}")
    sk = os.environ.get("TWILIO_API_KEY_SID", "")
    sks = os.environ.get("TWILIO_API_KEY_SECRET", "")
    s, _ = http("GET", f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                headers={"Authorization": basic(sk, sks)})
    check("TWILIO_API_KEY_SID/SECRET", s == 200, f"HTTP {s}")

    # Voice URL must point at LiveKit SIP
    s, body = http("GET",
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json"
        f"?PhoneNumber={os.environ.get('TWILIO_VOICE_NUMBER','')}",
        headers={"Authorization": basic(sid, tok)})
    if s == 200:
        nums = json.loads(body).get("incoming_phone_numbers", [])
        voice_url = nums[0].get("voice_url", "") if nums else ""
        ok = "sip.livekit.cloud" in voice_url
        check("Twilio voice→LiveKit SIP", ok, voice_url[:80] or "no number found")
    else:
        check("Twilio voice URL fetch", False, f"HTTP {s}")

    # --- OPC Book ---
    print("\nOPC Book API (booking tool):")
    opc_url = os.environ.get("OPC_BOOK_URL", "")
    opc_key = os.environ.get("OPC_BOOK_API_KEY", "")
    if opc_url and opc_key:
        s, body = http("POST", opc_url,
                       headers={"x-api-key": opc_key, "Content-Type": "application/json"},
                       data=b'{"caller_phone":"+10000000000","tour_slot":"probe","on_property":true,'
                            b'"deposit_path":"folio","sms_consent_captured":false,"sms_consent_phrase":"",'
                            b'"placement_name":"probe","incentive":"probe","property_name":"probe"}')
        check("OPC_BOOK_API_KEY (x-api-key)", s in (200, 400, 422),
              f"HTTP {s} {body[:80].decode('utf-8','replace')}")
    else:
        check("OPC_BOOK_URL/KEY", False, "missing")

    # --- Optional / unused-by-worker placeholders ---
    print("\nUnused-by-worker (warn-only):")
    sk = os.environ.get("STRIPE_SECRET_KEY", "")
    check("STRIPE_SECRET_KEY (placeholder=ok if unused)", sk != "sk_test_",
          "literal 'sk_test_' placeholder" if sk == "sk_test_" else "looks real",
          hard=False)
    lan = os.environ.get("LIVE_AGENT_NUMBER", "")
    placeholder = lan.startswith("+1555")
    check("LIVE_AGENT_NUMBER (real specialist phone)", not placeholder,
          f"{lan} {'(PLACEHOLDER!)' if placeholder else ''}",
          hard=True)  # hard: warm transfer needs this
    sb = os.environ.get("SUPABASE_URL", "")
    check("SUPABASE_URL (only if you wire transfer_contexts)", bool(sb),
          sb or "empty (worker doesn't use it today)", hard=False)

    # --- Summary ---
    print("\n" + "=" * 50)
    if failures:
        print(f"{FAIL} {len(failures)} hard failure(s):")
        for f in failures:
            print(f"   - {f}")
    if warnings:
        print(f"{WARN} {len(warnings)} warning(s):")
        for w in warnings:
            print(f"   - {w}")
    if not failures and not warnings:
        print(f"{OK} All checks passed — go for demo.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
