# Deedy — Architecture & Build Brief

> The Arrivia Virtual Booking Agent (VBA), live on LiveKit Cloud.
> Snapshot: 2026-05-03

---

## 1. Identity

- **Name**: Deedy (pronounced "Dee-dee" — the TTS receives the phonetic spelling "Deedee" so it reads it as a name, not letters).
- **Brand**: Arrivia — pronounced **"uh-RIH-vee-uh"** (three syllables). White-label across multiple resort partners.
- **Role**: Virtual Booking Agent. NOT called "AI" on calls (sounds scary). Called "virtual booking agent" — sounds like a job title.
- **Purpose**: Qualify guests for vacation-ownership preview presentations and book the appointment. Education-first, not a salesperson.
- **Pilot resort**: Westgate Lakes Resort & Spa (Orlando) — but Deedy is parameterized so any partner resort can be passed in via per-call metadata.

---

## 2. Voice + Brain Stack

| Layer | Provider / Model | Notes |
|---|---|---|
| **Speech-to-Text (STT)** | Deepgram Flux (`deepgram/flux-general`) | Semantic + acoustic turn detection built-in. Threshold tuned to 0.7 / 0.9 / 2000ms timeout to prevent mid-sentence cutoffs. |
| **Large Language Model (LLM)** | Grok 4.20 non-reasoning (`xai/grok-4.20-0309-non-reasoning`) | Better instruction-following than 4.1-fast on Deedy's long, structured persona. Temperature 0 for determinism, 400-token cap for brevity. |
| **Text-to-Speech (TTS)** | Rime mistv3 voice "lagoon" | Warm, calm female voice. Telephony-optimized. |
| **Voice Activity Detection (VAD)** | Silero | Drives interruption detection. |
| **Noise Cancellation** | Krisp BVCTelephony | 8 kHz / narrowband / SIP audio. |
| **Routing layer** | LiveKit Cloud (us-east) | All three providers above run via LiveKit Inference — single bill, lower latency, no extra API keys. |

**Why this stack**: dramatically more natural-sounding than xAI's bundled Grok Voice realtime model (which sounded robotic on early tests). ~$0.07/min fully loaded vs ~$0.0975/min on Retell + Claude Haiku.

---

## 3. Hard Compliance Rules

These are NEVER violated — baked into the system prompt as inviolable rules:

1. **18+ data-consent gate**: Deedy confirms the caller is eighteen or older BEFORE collecting any other information. Required for COPPA + state teen-privacy laws.
2. **Recording disclosure** in the first 10 seconds of every call (Florida is two-party consent).
3. **AI disclosure**: if asked "are you a robot / AI / a real person?", Deedy acknowledges: *"I'm a virtual booking agent — that means I'm AI-powered, but I'm here to help you book your preview just like a live agent would."* Never denies. Never pretends to be human.
4. **PCI absolute prohibition**: Deedy NEVER asks for, accepts, or repeats credit-card numbers, CVV, expiration, SSN, full DOB, bank info, or billing ZIP. If a caller starts to read card digits, she interrupts: *"Please stop — I do not take any payment or card information on this call. The 75-dollar deposit is handled separately."*
5. **No specific premium named**: never says "Disney tickets" or any specific reward — uses generic *"limited-time premium offer."* The actual premium is identified at booking confirmation by the welcome team.
6. **No quotes on pricing, financing, contract details, point values, expiration dates** — defers to the welcome team for any of those.
7. **White-label**: Deedy presents as the resort's assistant. Voxaris is invisible to callers. Arrivia is named once in the greeting (the platform brand).
8. **Two-strike rule (revised)**: Deedy does NOT drop the call on factual challenges or single "no" answers. The two-strike end applies only to explicit DNC, repeated "I'm not interested," or the same dispositive objection after one rebuttal.

---

## 4. The 9 Canonical OPC Qualification Gates

Per Arrivia's OPC (Off-Premise Contact) Qualification Guide:

| # | Gate | What's confirmed |
|---|---|---|
| 1 | Age | 25+ AND legally able to enter a contract |
| 2 | Household income | $50K+ per year, with discretionary travel income |
| 3 | Decision-makers | Both spouses/partners attend together |
| 4 | Creditworthiness | Valid major credit card (not prepaid), NOT in active bankruptcy |
| 5 | Employment status | Employed, self-employed, or retired with income |
| 6 | Tour history | No timeshare preview tour in the last 6–12 months |
| 7 | Residency | Outside local marketing area (~75 miles from resort) |
| 8 | Language | Comfortable with 90-minute English presentation |
| 9 | Attendance commitment | Willing to attend full 90–120 minute preview |

Deedy weaves these into a natural conversation, NOT a checklist interrogation. She uses information already shared during soft-discovery (e.g., if the caller mentioned bringing their wife, Deedy doesn't re-ask "who would attend?" — she confirms).

---

## 5. Conversation Flow (22 nodes)

Ported from a Retell flow JSON, reorganized as a single-prompt state machine. Stages:

1. **start_disclosures + 18+ gate** — agent's first message, confirms 18+ before anything else
2. **hook_and_permission** — pitch the offer, ask permission to qualify, capture caller's first name
3. **soft_qual** — 4 conversational questions (on/off-property, length of stay, traveling with whom, vacation frequency)
4–12. **9 hard qualification gates** (see section 4)
13. **schedule_offer** — propose 2 concrete tour slots
14. **deposit_explanation** — branches on `on_property`: folio hold (on-property) vs team-followup (off-property)
15. **confirm_and_sms_consent** — single-pass slot confirmation + SMS opt-in
16. **book_tool_call** — invoke `opc_book` (real booking endpoint)
17. **end_confirmed_tour** — read-back script, hangup
18. **end_graceful** — context-aware exit (DNC, not_eligible, not_interested, etc.)
19–22. **Objection handlers**: time, sales-resistance, spouse, general — each with a soft 2-strike rule

---

## 6. Tools Available to Deedy

| Tool | What it does |
|---|---|
| `lookup_qa` | Pulls canonical Arrivia answers from an 18-entry Q&A library (premium, presentation, deposit, eligibility, opt-out). Used the first time a caller asks any factual question Deedy's not 100% sure about. |
| `lookup_objection` | Pulls rebuttals from a 100-entry Top 100 Objections playbook. Used on any first-pass emotional/sales objection. |
| `opc_book` | Real booking call to the existing `arrivia-gvr.vercel.app/api/tools/opc-book` endpoint. Only fires after all 9 gates pass + slot + SMS opt-in. |
| `send_sms_confirmation` | Personalized SMS via SendBlue after `opc_book` returns success. Includes caller name, slot, deposit phrasing, premium reference. |
| `hangup_call` | Cleanly ends the call after the close. Tags the exit reason (qualified_and_booked, not_eligible, dnc, etc.) for analytics. |
| `detect_voicemail` | Classifies the line as voicemail and exits gracefully with a short callback message. |

---

## 7. Telephony

### Inbound
- **Number**: +1 (407) 258-6810 (LiveKit Phone Number, Windermere FL — Orlando metro)
- **Path**: PSTN → LiveKit SIP → dispatch rule (`SDR_ito8WVmoAGkV`) creates `inbound-XXXX` room → cloud agent auto-joins → Deedy speaks the inbound greeting
- **No Twilio in path** — direct LiveKit Phone Number, simplest possible routing

### Outbound
- **Number**: +1 (407) 289-0294 (Twilio Elastic SIP Trunk, Florida)
- **Path**: Orchestrator (test_sip_call.py / future /api/dial) dispatches the agent → agent creates SIP participant via LiveKit outbound trunk (`ST_GuYnNqX5KFor`) → INVITE to `voxaris-vba-livekit.pstn.twilio.com` → Twilio dials PSTN with caller-ID +14072890294
- Outbound carrier-ID supplied per-call via `sip_number` (the trunk allows wildcard numbers)

---

## 8. Deployment

- **Platform**: LiveKit Cloud (Build plan — upgrading to Ship $50/mo for cold-start prevention)
- **Agent ID**: `CA_3gZN7ciwyCzq`
- **Region**: us-east
- **Replicas**: 1 (Build tier), bumped to always-on with Ship
- **Dashboard**: https://cloud.livekit.io/projects/voxaris-vba-ks6ggp0s/agents/CA_3gZN7ciwyCzq
- **Repo**: `apps/agent/` (Python 3.12, livekit-agents 1.5.7)
- **Deploy command**: `lk agent deploy --silent`
- **Logs**: `lk agent logs --log-type=runtime`

### Environment variables (cloud agent secrets)
- `LIVEKIT_*` — auto-injected by LiveKit Cloud
- `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` — for outbound dialing
- `OPC_BOOK_URL` / `OPC_BOOK_API_KEY` — booking endpoint
- `SENDBLUE_API_KEY_ID` / `_SECRET_KEY` / `_FROM_NUMBER` — SMS confirmation
- `XAI_API_KEY` / `DEEPGRAM_API_KEY` / `RIME_API_KEY` — kept for fallback to direct provider APIs (currently unused; LiveKit Inference handles all three)

---

## 9. Cost (per-minute fully loaded)

| Item | Per minute |
|---|---|
| Twilio outbound voice | $0.0085 |
| LiveKit third-party SIP | $0.004 |
| LiveKit agent session | $0.01 (after free tier) |
| LiveKit Inference (STT + LLM + TTS) | ~$0.05 (after $5/mo Ship credits) |
| LiveKit observability (recording) | $0.005 |
| **Total** | **~$0.07–0.08/min** |

For a 5-minute qualification call: **~$0.40 fully loaded.**
SMS confirmation via SendBlue: ~$0.01–0.03 per successful booking.

vs Retell + Claude Haiku at $0.0975/min: roughly **27% cheaper on Build, dropping further as volume amortizes the $50 Ship base**.

---

## 10. Per-Call Personalization

These fields are passed via dispatch metadata at the start of each call and substituted throughout the persona:

| Variable | Source | Example |
|---|---|---|
| `property_name` | Per-resort dispatch metadata | "Westgate Lakes Resort & Spa" |
| `premium_offer` | Per-promo dispatch metadata | "limited-time premium offer" (default — never names specific premium on call) |
| `placement_name` | Per-QR dispatch metadata | "the pool deck" |
| `caller_name` / `caller_first_name` | Captured live during conversation | "Ethan" |
| `caller_phone` | Pulled from SIP attributes (inbound) or passed in metadata (outbound) | "+14078195809" |
| `slot_1` / `slot_2` | Per-resort calendar | "tomorrow at 10:30 AM" / "tomorrow at 2:15 PM" |
| `on_property` | Captured during soft_qual question 1 | true / false |

The same agent can be deployed for any Arrivia partner resort — only the metadata changes per dispatch.

---

## 11. Open Items / Roadmap

- **Voice cloning** — option to clone an Arrivia-branded custom voice via xAI Custom Voices or ElevenLabs once Arrivia provides a 120-second reference clip
- **Live agent transfer** — `transfer_to_human` tool wiring (currently ends gracefully and asks for callback)
- **Direct provider keys** — switch from LiveKit Inference markup to direct Deepgram/xAI/Rime keys at >5K calls/mo for ~40% inference cost reduction
- **Twilio Trust Hub registration** — to remove "Spam Likely" labeling on outbound calls from the +14072890294 DID
- **Ship plan upgrade** — $50/mo for cold-start prevention + 2 deployments (staging + prod)
- **Multi-tenant routing** — when Arrivia signs partner #2, switch to named-dispatch per partner

---

## 12. What Makes Deedy Sound Good

- **Memory & continuity rule** in the persona — Deedy reuses information from earlier in the call rather than re-asking
- **"Conversational, not interrogation"** framing — the 9 gates are woven naturally into the conversation
- **Single-pass SMS confirmation** — no repeated questions in the close
- **Warm goodbye** — Deedy waits a beat before hangup so the caller can say bye first
- **Eager EOT 0.7 + EOT 0.9 + 2000ms timeout** — Deepgram Flux endpointing tuned high enough that the agent doesn't cut off mid-sentence
- **`allow_interruptions=True`** — caller can interrupt; agent yields rather than steamrolling
- **Behavioral-signal listening** — Deedy is told to read disposable-income, decision-dynamics, travel-habit, personality-fit cues alongside the hard gates
