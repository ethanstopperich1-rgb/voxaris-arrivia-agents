# Voxaris Agents — Tech Truth Doc

**Last verified:** 2026-05-06 against `main` of [`voxaris-arrivia-agents`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents).
This is the authoritative reference for how Andie and Deedy are actually built. If the code disagrees with this doc, the code wins — open a PR fixing the doc.

---

## Production state at a glance

| | **Andie** (GVR fronter) | **Deedy** (Westgate VBA) |
|---|---|---|
| **Use case** | Outbound + inbound fronter for Government Vacation Rewards | After-hours QR-scan booking agent for Westgate Lakes |
| **Direction** | Inbound + outbound | Inbound only (caller scans QR → call triggered) |
| **Render service** | `voxaris-andie` (`srv-d7sdct3t6lks73attr20`) | `voxaris-deedy` |
| **LiveKit dispatch name** | `andie-gvr` | `deedy-vba` |
| **Public phone** | `+1 (689) 260-8790` | `+1 (407) 258-6810` |
| **Repo path** | `apps/andie/voxaris_andie/worker.py` | `apps/agent/voxaris_agent/worker.py` |
| **STT primary** | Deepgram Flux (tuned EOT) | Deepgram Flux (conservative EOT) |
| **LLM primary** | OpenAI GPT-4o-mini | xAI Grok 4.20 non-reasoning |
| **TTS primary** | Cartesia Sonic-3 (Jacqueline voice, emotion=content, speed=1.1) | Rime mistv3 (tundra voice) |
| **Persona size** | ~4k tokens (canonical structure) | ~6k tokens |
| **Tests** | `tests/test_smoke.py` + `tests/test_behavior.py` | `tests/test_smoke.py` |

---

## High-level architecture

```
                    ┌─────────────────────────┐
                    │   Twilio Elastic SIP    │  ◀── PSTN
                    │   Trunks (per number)   │
                    └────────────┬────────────┘
                                 │ SIP
                    ┌────────────▼────────────┐
                    │   LiveKit Cloud SFU     │  ◀── (us-east, virginia)
                    │   Inbound trunk + SIP   │
                    │   dispatch rules        │
                    └────────────┬────────────┘
                                 │ WebSocket job pull
              ┌──────────────────┼──────────────────┐
              │                                     │
   ┌──────────▼──────────┐               ┌──────────▼──────────┐
   │   voxaris-andie     │               │   voxaris-deedy     │
   │   (Render worker)   │               │   (Render worker)   │
   │                     │               │                     │
   │   Python 3.13       │               │   Python 3.13       │
   │   livekit-agents    │               │   livekit-agents    │
   │   Docker, starter   │               │   Docker, starter   │
   └─────────┬───────────┘               └─────────┬───────────┘
             │                                     │
             │  inference.{STT, LLM, TTS}          │
             │  (LiveKit Inference proxy)          │
             ▼                                     ▼
   ┌─────────────────────────────────────────────────────┐
   │   Deepgram (STT)  •  OpenAI / xAI (LLM)             │
   │   Cartesia / Rime (TTS)  •  Silero (VAD)            │
   └─────────────────────────────────────────────────────┘
             │
             │  POST telemetry events
             ▼
   ┌─────────────────────────────────────────────────────┐
   │   arrivia.voxaris.io/api/agent/events               │
   │   (Next.js dashboard, Vercel)                       │
   │                                                     │
   │   Supabase Postgres ◀── call sessions, transcripts, │
   │                         tool invocations, members   │
   └─────────────────────────────────────────────────────┘
```

**Two repos:**
- [`voxaris-arrivia-agents`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents) — this monorepo. Andie + Deedy Python workers.
- [`voxaris-arrivia-dashboard`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-dashboard) — Next.js dashboard, telemetry endpoints, auth. Hosted on Vercel.

---

## Andie — GVR Fronter Agent

### What Andie does
Replaces Arrivia's Philippines fronter team for Government Vacation Rewards. Outbound: dials the cold/warm propensity base, qualifies travel intent, warm-transfers to a closer with discovery context loaded. Inbound: handles return calls (50% of Arrivia's inbound traces back to outbound footprint).

### Brain stack (verified [worker.py L1465-L1535](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/andie/voxaris_andie/worker.py))

| Layer | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| **STT** | `deepgram/flux-general`<br/>`eager_eot=0.5`, `eot=0.7`, `eot_timeout_ms=800` | `deepgram/nova-3` | — |
| **LLM** | `openai/gpt-4o-mini`<br/>`temp=0`, `max_tokens=180` | `openai/gpt-4.1-mini`<br/>`temp=0`, `max_tokens=180` | `xai/grok-4.20-0309-non-reasoning`<br/>`temp=0`, `max_tokens=180` |
| **TTS** | `cartesia/sonic-3`<br/>voice `e07c00bc-...c8bc` (Jacqueline)<br/>`emotion="content"`, `speed=1.1` | `rime/mistv3`<br/>voice `steppe`, 16kHz | `rime/arcana`<br/>voice `luna` |
| **VAD** | `silero.VAD.load()` | — | — |
| **Turn handling** | `turn_detection="stt"` (Flux native EOT) | — | — |

**Why this stack:**
- **Flux for STT** — purpose-built for voice agents, ~260ms model-integrated end-of-turn detection. Native barge-in handling at Nova-3-level accuracy.
- **GPT-4o-mini for LLM** — cheap (~$0.15/M in, $0.60/M out), fast, fine for a narrow fronter script. Cross-provider fallback chain (OpenAI → OpenAI different model → xAI) so a single-provider outage can't drop calls.
- **Cartesia Sonic-3 for TTS** — warmer than Rime mistv3 on PSTN. The cloned voice ID is a Cartesia library voice (Jacqueline), not a custom clone (custom clones don't work through LiveKit Inference yet).

### Tool registry (6 tools, [worker.py L1017+](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/andie/voxaris_andie/worker.py))

| Tool | Purpose | When LLM calls it |
|---|---|---|
| `lookup_faq(question_text)` | Canonical GVR FAQ | First factual question (benefits, eligibility, redemption) |
| `verify_me_to_caller()` | Returns verification the caller can use to confirm Andie is legit | Caller asks "is this a scam / how did you get my number / how do I know you're real" |
| `lookup_objection(objection_text)` | Pulls a canonical rebuttal from 10 objection categories | First objection on any axis (trust/time/fit/cost/privacy/etc.) |
| `send_scheduler_link(channel, destination, caller_name)` | Texts/emails a Microsoft Bookings link | Caller declines warm transfer but agrees to schedule |
| `transfer_to_specialist(reason, brief)` | Warm-transfer via dial-and-bridge (NOT SIP REFER) | Caller accepts transfer AND has shared ≥2 discovery answers |
| `hangup_call(reason)` | End the call cleanly with a disposition tag | Transfer complete / scheduled / DNC / wrong-person / not-interested |

**Discovery-before-transfer rule (Jay's principle):** Andie does NOT call `transfer_to_specialist` cold. The persona requires at least 2 of {destination, timeframe, who's coming, occasion} before any transfer. Per Jay (VP Memberships, Arrivia): *"The best transfers are the ones where we got good discovery. The more information we get, the better the specialist can meet the need."*

### Persona structure ([worker.py L458+](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/andie/voxaris_andie/worker.py))

Follows the LiveKit canonical prompting structure verified against [docs.livekit.io/agents/start/prompting](https://docs.livekit.io/agents/start/prompting):

1. **Identity** — Andie, virtual benefits guide, GVR/Arrivia, NOT government
2. **Output rules** — plain text only, no markdown, max 3 sentences, ≤18 words/sentence
3. **Personality** — observable behaviors (acknowledgments, observations, sparing first-name use, `[laughter]` allowed once max)
4. **Pauses and filler words** — explicit anti-pattern: do NOT insert `<break>` tags into replies
5. **Phrase variation** — rotate openers to avoid AI-tell repetition
6. **Emotion** — calm baseline, stronger emotion only at genuine moments
7. **Conversational flow** — inbound + outbound flows, four pillars, verbatim rebuttals
8. **Tools** — usage rules
9. **Goals** — warm transfer with discovery context = best outcome
10. **Guardrails** — AI identity, FTC-safe language, scam-pattern blocklist, numbers/specifics rule, PII prohibition, call hygiene, dispositive vs non-dispositive
11. **User information** — dynamic vars (name, ID, incentive, transfer bonus, etc.)

**Total prompt size:** ~16,500 chars / ~2,600 words / ~4,000 tokens.

### Greeting bypass (latency optimization)
Andie's verbatim opener is played via `session.say()` directly to TTS — bypassing the LLM round-trip. The opener templates live in [`OPENER_INBOUND_VERBATIM` and `OPENER_OUTBOUND_VERBATIM_TEMPLATE`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/andie/voxaris_andie/worker.py). Saves ~1-2s on first response.

### Tests ([apps/andie/tests/](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/tree/main/apps/andie/tests))

| File | Coverage | Runs without API key? |
|---|---|---|
| `test_smoke.py` | 17 tests — module imports, persona invariants (brand, FTC, PCI, four pillars, rebuttals), greeting structure, QA loaded, objection lookup, metadata parser | ✅ yes |
| `test_behavior.py` | 7 tests using LiveKit's `AgentSession.run` + `result.expect.judge` framework — basic conversation, plain-text output, AI-identity disclosure, FTC government correction, PII refusal, no-cold-transfer rule, credibility verification | ❌ skips when `OPENAI_API_KEY` unset |

Run with: `cd apps/andie && uv run pytest tests/`

---

## Deedy — Westgate Lakes VBA Agent

### What Deedy does
Inbound-only after-hours booking agent for Westgate Lakes Resort & Spa. Guest scans a QR code in their villa → triggers a phone call → Deedy walks them through a complimentary tour booking with the OPC welcome team. Designed for the Arrivia VBA (Virtual Booking Agent) pilot, separate from GVR.

### Brain stack (verified [worker.py L1842-L1910](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/agent/voxaris_agent/worker.py))

| Layer | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| **STT** | `deepgram/flux-general`<br/>`eager_eot=0.7`, `eot=0.9`, `eot_timeout_ms=2000` *(conservative)* | `deepgram/nova-3` | — |
| **LLM** | `xai/grok-4.20-0309-non-reasoning`<br/>`temp=0`, `max_tokens=180`, `parallel_tool_calls=False` | `xai/grok-4-1-fast-non-reasoning`<br/>(deprecating ~May 16) | `openai/gpt-4.1-mini` |
| **TTS** | `rime/mistv3`<br/>voice `tundra`, 16kHz | `rime/arcana`<br/>voice `luna` | `cartesia/sonic-2`<br/>voice `warm-female` |
| **VAD** | `silero.VAD.load()` | — | — |

**Why Deedy diverges from Andie:**
- **Conservative STT EOT** — Deedy is in a relaxed booking-flow context (after-hours, guests not in a hurry). The 2000ms silence ceiling is fine; we have not retuned to match Andie's tighter values yet.
- **Grok primary LLM** — Deedy was built first, before the GPT-4o-mini swap on Andie. Grok 4-1-fast-non-reasoning fallback **deprecates ~May 16, 2026** — Deedy needs the same swap Andie got.
- **Rime primary TTS** — original "tundra" voice was selected for the Westgate brand. Cartesia stays as deepest fallback.

### Tool registry (8 tools, [worker.py](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/agent/voxaris_agent/worker.py))

| Tool | Purpose |
|---|---|
| `opc_book` | Submit booking to OPC (Off-Property Contact) welcome team |
| `send_sms_confirmation` | Text the guest a booking confirmation via Sendblue |
| `detect_voicemail` | Detect if the call hit voicemail vs a live person |
| `lookup_qa` | Canonical Westgate FAQ |
| `note_uncertainty` | Log when Deedy is uncertain (for review) |
| `transfer_to_human` | Warm-transfer to live OPC welcome rep |
| `lookup_objection` | Objection rebuttal playbook (shared pattern with Andie) |
| `hangup_call` | End the call cleanly |

### Tests
[`apps/agent/tests/test_smoke.py`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/apps/agent/tests) — module imports, brand invariants, persona checks. No behavior-test coverage yet (TODO — bring up to Andie's level).

---

## Shared infrastructure

### LiveKit Cloud
- **Region:** us-east (Virginia) — matches Render workers, minimizes latency to LiveKit SFU
- **Inbound:** Twilio SIP trunk → LiveKit SIP inbound trunk → dispatch rule routes to `andie-gvr` or `deedy-vba` based on the dialed number
- **Outbound:** worker calls `inference.LLM.tools` `transfer_to_specialist` → dial-and-bridge via `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`
- **Inference:** all model calls go through LiveKit Inference (no separate Deepgram/OpenAI/etc. keys needed at runtime — billed via LiveKit)

### Twilio
- **Andie inbound trunk:** TWILIO_VOICE_NUMBER → LiveKit SIP inbound
- **Deedy inbound trunk:** same trunk, different DID
- **Outbound trunk:** Twilio Elastic SIP for warm-transfer leg

### Supabase
- Member data, call sessions, tool invocations, dispositions, transcripts (linked from S3 recordings)
- One project shared across both agents
- Schema migrations in [`supabase/migrations/`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/tree/main/supabase/migrations)

### Observability
- Per-call telemetry POST to `https://arrivia.voxaris.io/api/agent/events` ([dashboard repo](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-dashboard))
- Events: `session_started`, `tool_invocation`, `transfer_initiated`, `session_usage_updated`, `session_ended`
- Auth: `APP_API_KEY` shared between agents and the dashboard
- Recordings: optional, S3-backed, gated on `RECORDING_ENABLED=true`

### Render deployment
- Both services use Docker workers, plan: `starter` ($7/mo each, 0.5 CPU / 512MB)
- Auto-deploy on push to `main`
- Blueprint: [`render.yaml`](https://github.com/ethanstopperich1-rgb/voxaris-arrivia-agents/blob/main/render.yaml)
- Env vars set in Render dashboard (never committed; `sync: false` in the blueprint)

---

## Compliance rules (baked into both agents)

These are non-negotiable and enforced by the persona prompt + tool design:

| Rule | Enforcement |
|---|---|
| **Recording disclosure within 10s** | Hardcoded into the verbatim opener — played via `session.say()` before any LLM-generated speech |
| **AI identity disclosure if asked** | Persona Guardrails section — "I'm a virtual benefits guide, smart software, not a live person" |
| **TCPA consent verification** | Pre-dial — leads with `tcpa_consent=false` are filtered out at queue time on the dashboard side |
| **DNC + litigator suppression** | Pre-dial scrub against federal DNC + litigator lists |
| **No FTC-flagged language** | Persona explicit blocklist — "government-approved", "officially endorsed", "DoD-backed", etc. |
| **No PII collection** | Persona ABSOLUTE PROHIBITION section — Andie/Deedy interrupt the caller if they start reading card digits |
| **Two-party consent states** (CA, WA, HI, FL) | Recording disclosure satisfies; tracked via `state` field on the lead record |
| **No Select Access pricing on Andie** | Persona Guardrails — `$3,499`, `5,000 points`, `75,000 points`, `5x earnings`, `APR`, `financing` are all forbidden tokens |

---

## Where things live (file map)

```
voxaris-arrivia-agents/                    ← agent monorepo
├── apps/
│   ├── andie/                             ← Andie GVR
│   │   ├── voxaris_andie/
│   │   │   └── worker.py                  ← single source of truth (1548 LOC)
│   │   ├── tests/
│   │   │   ├── test_smoke.py              ← 17 unit tests
│   │   │   └── test_behavior.py           ← 7 LiveKit-framework tests
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── livekit.toml
│   │   └── .env.example                   ← required env vars
│   │
│   └── agent/                             ← Deedy VBA
│       ├── voxaris_agent/
│       │   └── worker.py                  ← single source of truth (2056 LOC)
│       ├── tests/test_smoke.py
│       ├── Dockerfile
│       └── .env.example
│
├── render.yaml                            ← Render Blueprint (both services)
├── supabase/migrations/                   ← schema
└── docs/
    ├── AGENTS-TRUTH.md                    ← THIS FILE
    ├── build-plan.md
    ├── compliance.md
    ├── env.md
    └── livekit/                           ← LiveKit-specific notes
```

```
voxaris-arrivia-dashboard/                 ← Next.js dashboard repo
├── app/api/agent/events/route.ts          ← telemetry sink
├── app/api/retell/inbound/route.ts        ← Retell inbound (legacy, also handles RVM callbacks)
├── app/api/rvm/                           ← RVM Cowboy (separate product)
├── app/dashboard/                         ← live ops dashboard
└── lib/rvm/                               ← RVM Cowboy generation pipeline
```

---

## Per-agent commit lineage (most recent significant work)

### Andie — May 5/6, 2026
| Commit | What |
|---|---|
| `60b3c24` | Cut break-tag spam — too many SSML pauses everywhere |
| `baa7520` | TTS speed 0.95 → 1.1 (was too slow) |
| `f1c2d23` | Cartesia-native realism: emotion=content, [laughter], aligned transcripts |
| `ced7836` | Optimize for LiveKit voice-agent best practices (canonical structure, tests) |
| `f76c035` | Kill dramatic pauses + slow first response (EOT retune, opener bypasses LLM) |
| `cd350a3` | LLM primary → OpenAI GPT-4o-mini |
| `d36b500` | STT primary back to Flux (was wrong to demote) |
| `032a6e5` | TTS primary → Cartesia Sonic-3 (cloned voice) |

### Deedy — last touched
Older — primarily during the ARDA demo build. **Not yet retuned to match Andie's optimization baseline** (see TODO below).

---

## Known TODOs / debt

1. **Deedy LLM swap** — `xai/grok-4-1-fast-non-reasoning` fallback is deprecating ~May 16, 2026. Same swap Andie got (→ `openai/gpt-4.1-mini` + `gpt-4o-mini`).
2. **Deedy STT EOT retune** — currently on the conservative 0.9/2000ms tuning. Andie was retuned to 0.7/800ms after live testing showed the conservative values felt halting. Same retune likely warranted on Deedy.
3. **Deedy persona** — not yet refactored to LiveKit canonical structure. Still ~6k tokens with older section ordering.
4. **Deedy behavior tests** — only `test_smoke.py` exists. No `AgentSession.run` framework coverage.
5. **Phase D — handoff architecture** — both agents are currently monolithic. LiveKit best practice: split into greeting → discovery → transfer agents using `Agent` handoffs. Real refactor; deferred until pilot greenlit.
6. **Voice options** — Andie persona only documents 1 production voice. Per Arrivia feedback (May 6), need 5-voice picker (3F / 2M) implemented in the dashboard for Jay's team to A/B before pilot go-live.

---

## How to verify any claim in this doc

1. **STT/LLM/TTS config** → grep `inference.STT|inference.LLM|inference.TTS` in the worker.py
2. **Tool registry** → grep `@function_tool` in the worker.py
3. **Render deploy state** → `curl https://api.render.com/v1/services/{srv}/deploys?limit=5` with the Render API key
4. **Persona structure** → render the persona via `from voxaris_andie.worker import render_persona; print(render_persona())`
5. **Compliance rules** → grep the persona text for `Guardrails`, `FTC`, `SSN`, `Sensitive data`

If you find anything in this doc that doesn't match the code: open a PR fixing the doc. The code is the truth.

---

*Maintained by: Ethan Stopperich (Voxaris). Verified against `main` on 2026-05-06.*
